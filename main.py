from __future__ import annotations
from dotenv import load_dotenv
import json
import os
import random
import sys
import time
from datetime import datetime, timedelta, timezone
from typing import Any, Dict, List, Optional, Tuple

import gspread
import requests
from google.oauth2.service_account import Credentials

if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8")
if hasattr(sys.stderr, "reconfigure"):
    sys.stderr.reconfigure(encoding="utf-8")

# =========================
# CONFIG
# =========================
CREDS_FILE = os.getenv("GOOGLE_CREDS_FILE", "creds.json")
load_dotenv()

TELEGRAM_BOT_TOKEN = os.getenv("TELEGRAM_BOT_TOKEN")
TELEGRAM_CHAT_ID = os.getenv("TELEGRAM_CHAT_ID")
SPREADSHEET_NAME = os.getenv("SPREADSHEET_NAME", "FB Groups Input")
INPUT_WORKSHEET_NAME = os.getenv("INPUT_WORKSHEET_NAME", "input_groups")
OUTPUT_WORKSHEET_NAME = os.getenv("OUTPUT_WORKSHEET_NAME", "crawl_output")

GROUP_URL_COLUMN = os.getenv("GROUP_URL_COLUMN", "group_url")

APIFY_TOKEN = os.getenv("APIFY_TOKEN")
APIFY_ACTOR_ID = os.getenv("APIFY_ACTOR_ID", "apify~facebook-groups-scraper")

RESULTS_AMOUNT = int(os.getenv("RESULTS_AMOUNT", "20"))
LOOKBACK_DAYS = int(os.getenv("LOOKBACK_DAYS", "1"))

REQUEST_TIMEOUT = int(os.getenv("REQUEST_TIMEOUT", "300"))

# Delay cơ bản giữa các group
MIN_DELAY_SECONDS = float(os.getenv("MIN_DELAY_SECONDS", "4"))
MAX_DELAY_SECONDS = float(os.getenv("MAX_DELAY_SECONDS", "9"))

# Retry settings
MAX_RETRIES = int(os.getenv("MAX_RETRIES", "3"))
BACKOFF_BASE_SECONDS = float(os.getenv("BACKOFF_BASE_SECONDS", "3"))

# Optional proxy support
HTTP_PROXY = os.getenv("HTTP_PROXY")
HTTPS_PROXY = os.getenv("HTTPS_PROXY")

VN_TZ = timezone(timedelta(hours=7))

if os.getenv("GOOGLE_CREDS") and not os.path.exists("creds.json"):
    with open("creds.json", "w", encoding="utf-8") as f:
        f.write(os.getenv("GOOGLE_CREDS"))


def send_telegram(msg):
    if not TELEGRAM_BOT_TOKEN or not TELEGRAM_CHAT_ID:
        print("⚠️ Thiếu TELEGRAM config")
        return

    url = f"https://api.telegram.org/bot{TELEGRAM_BOT_TOKEN}/sendMessage"

    payload = {"chat_id": TELEGRAM_CHAT_ID, "text": msg}

    requests.post(url, json=payload)


# =========================
# GOOGLE SHEETS
# =========================
def get_gspread_client() -> gspread.Client:
    scopes = [
        "https://www.googleapis.com/auth/spreadsheets",
        "https://www.googleapis.com/auth/drive",
    ]
    creds = Credentials.from_service_account_file(CREDS_FILE, scopes=scopes)
    return gspread.authorize(creds)


def get_sheet_and_input_worksheet() -> Tuple[gspread.Spreadsheet, gspread.Worksheet]:
    client = get_gspread_client()
    sheet = client.open(SPREADSHEET_NAME)
    worksheet = sheet.worksheet(INPUT_WORKSHEET_NAME)
    return sheet, worksheet


def get_group_urls() -> List[str]:
    _, worksheet = get_sheet_and_input_worksheet()
    records = worksheet.get_all_records()

    group_urls: List[str] = []
    seen = set()

    for row in records:
        raw_url = row.get(GROUP_URL_COLUMN, "")
        url = str(raw_url).strip()

        if not url:
            continue
        if url in seen:
            continue

        seen.add(url)
        group_urls.append(url)

    return group_urls


def get_or_create_output_worksheet(sheet: gspread.Spreadsheet) -> gspread.Worksheet:
    try:
        worksheet = sheet.worksheet(OUTPUT_WORKSHEET_NAME)
    except gspread.exceptions.WorksheetNotFound:
        worksheet = sheet.add_worksheet(title=OUTPUT_WORKSHEET_NAME, rows=3000, cols=20)
    return worksheet


# =========================
# NETWORK / REQUEST
# =========================
def get_requests_session() -> requests.Session:
    session = requests.Session()
    if HTTP_PROXY or HTTPS_PROXY:
        session.proxies.update(
            {
                "http": HTTP_PROXY or "",
                "https": HTTPS_PROXY or "",
            }
        )
    session.headers.update(
        {
            "User-Agent": (
                "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
                "AppleWebKit/537.36 (KHTML, like Gecko) "
                "Chrome/124.0.0.0 Safari/537.36"
            )
        }
    )
    return session


def random_sleep(
    min_seconds: float = MIN_DELAY_SECONDS, max_seconds: float = MAX_DELAY_SECONDS
) -> None:
    delay = random.uniform(min_seconds, max_seconds)
    print(f"Ngủ {delay:.2f}s để giảm tần suất request...")
    time.sleep(delay)


def backoff_sleep(attempt: int) -> None:
    """
    Exponential backoff + jitter
    """
    base = BACKOFF_BASE_SECONDS * (2 ** (attempt - 1))
    jitter = random.uniform(0.5, 2.0)
    delay = base + jitter
    print(f"Retry sau {delay:.2f}s...")
    time.sleep(delay)


# =========================
# APIFY CRAWL
# =========================
def crawl_group(group_url: str) -> List[Dict[str, Any]]:
    """
    Gọi Apify actor để crawl 1 group Facebook.
    Có retry/backoff để giảm lỗi tạm thời / rate limit.
    """
    if not APIFY_TOKEN:
        raise ValueError("Thiếu APIFY_TOKEN. Hãy set biến môi trường APIFY_TOKEN.")

    api_url = (
        f"https://api.apify.com/v2/acts/{APIFY_ACTOR_ID}/run-sync-get-dataset-items"
    )

    payload = {
        "startUrls": [{"url": group_url}],
        "resultsLimit": RESULTS_AMOUNT,
        "viewOption": "CHRONOLOGICAL",
    }

    params = {
        "token": APIFY_TOKEN,
        "format": "json",
        "clean": "true",
    }

    session = get_requests_session()
    last_error: Optional[Exception] = None

    for attempt in range(1, MAX_RETRIES + 1):
        try:
            response = session.post(
                api_url,
                params=params,
                json=payload,
                timeout=REQUEST_TIMEOUT,
            )

            # Retry cho các lỗi dễ là tạm thời
            if response.status_code in (429, 500, 502, 503, 504):
                raise requests.HTTPError(
                    f"HTTP {response.status_code} - temporary error / possible rate limit"
                )

            response.raise_for_status()

            data = response.json()
            if not isinstance(data, list):
                return []
            return data

        except Exception as exc:
            last_error = exc
            print(f"Lần thử {attempt}/{MAX_RETRIES} thất bại cho group: {group_url}")
            print(f"Lỗi: {exc}")

            if attempt < MAX_RETRIES:
                backoff_sleep(attempt)
            else:
                break

    raise RuntimeError(
        f"Crawl thất bại sau {MAX_RETRIES} lần thử. Lỗi cuối: {last_error}"
    )


# =========================
# PROCESS POSTS
# =========================
def parse_time(time_str: str) -> datetime:
    return datetime.fromisoformat(time_str.replace("Z", "+00:00"))


def calc_engagement(post: Dict[str, Any]) -> int:
    likes = int(post.get("likesCount", 0) or 0)
    comments = int(post.get("commentsCount", 0) or 0)
    shares = int(post.get("sharesCount", 0) or 0)
    return likes + comments + shares


def find_top_post_24h(posts: List[Dict[str, Any]]) -> Optional[Dict[str, Any]]:
    now_utc = datetime.now(timezone.utc)
    one_day_ago = now_utc - timedelta(days=LOOKBACK_DAYS)

    filtered_posts: List[Dict[str, Any]] = []

    for post in posts:
        time_str = post.get("time")
        if not time_str:
            continue

        try:
            post_time = parse_time(time_str)
        except Exception:
            continue

        if post_time < one_day_ago:
            continue

        engagement = calc_engagement(post)

        filtered_posts.append(
            {
                "group_url": post.get("inputUrl", ""),
                "group_title": post.get("groupTitle", ""),
                "user": post.get("user", {}).get("name", ""),
                "time": post_time,
                "url": post.get("url", ""),
                "text": post.get("text", ""),
                "likesCount": int(post.get("likesCount", 0) or 0),
                "commentsCount": int(post.get("commentsCount", 0) or 0),
                "sharesCount": int(post.get("sharesCount", 0) or 0),
                "engagement": engagement,
            }
        )

    if not filtered_posts:
        return None

    return max(filtered_posts, key=lambda x: x["engagement"])


# =========================
# OUTPUT SHEET
# =========================
def format_block(
    worksheet: gspread.Worksheet, summary_row: int, header_row: int, total_columns: int
) -> None:
    requests_body = {
        "requests": [
            {
                "repeatCell": {
                    "range": {
                        "sheetId": worksheet.id,
                        "startRowIndex": summary_row - 1,
                        "endRowIndex": summary_row,
                        "startColumnIndex": 0,
                        "endColumnIndex": total_columns,
                    },
                    "cell": {
                        "userEnteredFormat": {
                            "backgroundColor": {
                                "red": 0.18,
                                "green": 0.49,
                                "blue": 0.20,
                            },
                            "textFormat": {
                                "bold": True,
                                "foregroundColor": {
                                    "red": 1,
                                    "green": 1,
                                    "blue": 1,
                                },
                            },
                        }
                    },
                    "fields": "userEnteredFormat(backgroundColor,textFormat)",
                }
            },
            {
                "repeatCell": {
                    "range": {
                        "sheetId": worksheet.id,
                        "startRowIndex": header_row - 1,
                        "endRowIndex": header_row,
                        "startColumnIndex": 0,
                        "endColumnIndex": total_columns,
                    },
                    "cell": {
                        "userEnteredFormat": {
                            "backgroundColor": {
                                "red": 0.11,
                                "green": 0.56,
                                "blue": 0.95,
                            },
                            "textFormat": {
                                "bold": True,
                                "foregroundColor": {
                                    "red": 1,
                                    "green": 1,
                                    "blue": 1,
                                },
                            },
                        }
                    },
                    "fields": "userEnteredFormat(backgroundColor,textFormat)",
                }
            },
        ]
    }

    worksheet.spreadsheet.batch_update(requests_body)


def write_results_to_output_sheet(run_results: List[Dict[str, Any]]) -> None:
    client = get_gspread_client()
    sheet = client.open(SPREADSHEET_NAME)
    worksheet = get_or_create_output_worksheet(sheet)

    crawl_time = datetime.now(VN_TZ).strftime("%Y-%m-%d %H:%M:%S")
    crawled_count = len(run_results)

    headers = [
        "crawl_time",
        "group_url",
        "group_title",
        "crawl_status",
        "error_message",
        "posts_fetched",
        "has_top_post_24h",
        "user",
        "time_vn",
        "url",
        "likesCount",
        "commentsCount",
        "sharesCount",
        "engagement",
        "text",
    ]

    existing_values = worksheet.get_all_values()
    rows_to_add: List[List[Any]] = []

    # Nếu đã có dữ liệu thì thêm 1 dòng trống tách block
    if existing_values:
        rows_to_add.append([""] * len(headers))

    success_count = sum(1 for x in run_results if x["crawl_status"] == "SUCCESS")
    no_post_count = sum(1 for x in run_results if x["crawl_status"] == "NO_POST_24H")
    error_count = sum(1 for x in run_results if x["crawl_status"] == "CRAWL_ERROR")

    rows_to_add.append(
        [
            f"CRAWL TIME: {crawl_time}",
            f"GROUPS: {crawled_count}",
            f"SUCCESS: {success_count}",
            f"NO_POST_24H: {no_post_count}",
            f"ERROR: {error_count}",
            "",
            "",
            "",
            "",
            "",
            "",
            "",
            "",
            "",
            "",
        ]
    )

    rows_to_add.append(headers)

    for item in run_results:
        top_post = item.get("top_post")
        if top_post and top_post.get("time"):
            vn_time = top_post["time"].astimezone(VN_TZ).strftime("%Y-%m-%d %H:%M:%S")
        else:
            vn_time = ""

        rows_to_add.append(
            [
                crawl_time,
                item.get("group_url", ""),
                item.get("group_title", ""),
                item.get("crawl_status", ""),
                item.get("error_message", ""),
                item.get("posts_fetched", 0),
                item.get("has_top_post_24h", False),
                top_post.get("user", "") if top_post else "",
                vn_time,
                top_post.get("url", "") if top_post else "",
                top_post.get("likesCount", 0) if top_post else 0,
                top_post.get("commentsCount", 0) if top_post else 0,
                top_post.get("sharesCount", 0) if top_post else 0,
                top_post.get("engagement", 0) if top_post else 0,
                top_post.get("text", "") if top_post else "",
            ]
        )

    worksheet.append_rows(rows_to_add, value_input_option="USER_ENTERED")

    total_rows = len(worksheet.get_all_values())
    start_row = total_rows - len(rows_to_add) + 1

    summary_row = start_row + (1 if existing_values else 0)
    header_row = summary_row + 1

    format_block(worksheet, summary_row, header_row, len(headers))


# =========================
# SAVE LOCAL JSON
# =========================
def save_results_to_json(
    run_results: List[Dict[str, Any]], filename: str = "top_posts_results.json"
) -> None:
    serializable_results = []

    for item in run_results:
        top_post = item.get("top_post")
        safe_top_post = None

        if top_post:
            safe_top_post = {
                **top_post,
                "time": top_post["time"].isoformat() if top_post.get("time") else None,
            }

        serializable_results.append(
            {
                **item,
                "top_post": safe_top_post,
            }
        )

    with open(filename, "w", encoding="utf-8") as f:
        json.dump(serializable_results, f, ensure_ascii=False, indent=2)


# =========================
# MAIN
# =========================
def main() -> None:
    try:
        groups = get_group_urls()
    except FileNotFoundError:
        print(f"Không tìm thấy file credentials: '{CREDS_FILE}'.")
        sys.exit(1)
    except gspread.exceptions.SpreadsheetNotFound:
        print(f"Không tìm thấy Google Sheet '{SPREADSHEET_NAME}'.")
        sys.exit(1)
    except gspread.exceptions.WorksheetNotFound:
        print(f"Không tìm thấy worksheet input '{INPUT_WORKSHEET_NAME}'.")
        sys.exit(1)
    except Exception as exc:
        print(f"Lỗi khi đọc Google Sheet: {exc}")
        sys.exit(1)

    if not groups:
        print("Không có group nào trong input sheet.")
        return

    print("Danh sách group đọc được:")
    for idx, group in enumerate(groups, start=1):
        print(f"{idx}. {group}")

    run_results: List[Dict[str, Any]] = []

    print("\n=== BẮT ĐẦU CRAWL ===")
    print("Gợi ý vận hành an toàn:")
    print("- Crawl tuần tự từng group")
    print("- Random delay giữa các request")
    print("- Retry + exponential backoff khi lỗi tạm thời / rate limit")
    print("- Nếu scale lớn, nên dùng proxy hoặc chia batch theo đợt")
    print("- Không nên crawl quá dày trên cùng 1 IP trong thời gian ngắn")

    for idx, group in enumerate(groups, start=1):
        print(f"\n=== Đang crawl group {idx}/{len(groups)} ===")
        print(group)

        result_item: Dict[str, Any] = {
            "group_url": group,
            "group_title": "",
            "crawl_status": "",
            "error_message": "",
            "posts_fetched": 0,
            "has_top_post_24h": False,
            "top_post": None,
        }

        try:
            posts = crawl_group(group)
            result_item["posts_fetched"] = len(posts)
            print(f"Số post crawl được: {len(posts)}")

            top_post = find_top_post_24h(posts)

            if posts:
                first_title = posts[0].get("groupTitle", "")
                result_item["group_title"] = first_title

            if not top_post:
                result_item["crawl_status"] = "NO_POST_24H"
                result_item["has_top_post_24h"] = False
                print("Không tìm thấy post nào trong 24h gần nhất.")
            else:
                result_item["crawl_status"] = "SUCCESS"
                result_item["has_top_post_24h"] = True
                result_item["top_post"] = top_post
                result_item["group_title"] = top_post.get(
                    "group_title", result_item["group_title"]
                )

                vn_time = top_post["time"].astimezone(VN_TZ)
                print("Top post:")
                print("User:", top_post["user"])
                print("Time (VN):", vn_time.strftime("%Y-%m-%d %H:%M:%S"))
                print("URL:", top_post["url"])
                print(
                    f"Engagement: {top_post['engagement']} = "
                    f"{top_post['likesCount']} likes + "
                    f"{top_post['commentsCount']} comments + "
                    f"{top_post['sharesCount']} shares"
                )
                print("Text:", top_post["text"][:300])

        except Exception as exc:
            result_item["crawl_status"] = "CRAWL_ERROR"
            result_item["error_message"] = str(exc)
            print(f"Lỗi khi crawl group {group}: {exc}")

        run_results.append(result_item)

        if result_item["crawl_status"] == "SUCCESS" and result_item["top_post"]:
            top = result_item["top_post"]

            msg = (
                f"🔥 TOP POST\n"
                f"Group: {result_item['group_title']}\n"
                f"Engagement: {top['engagement']}\n"
                f"Link: {top['url']}"
            )

            send_telegram(msg)

        # Delay ngẫu nhiên giữa các group
        if idx < len(groups):
            random_sleep()

    print("\n=== TỔNG KẾT ===")
    print(f"Tổng số group xử lý: {len(run_results)}")
    print(f"SUCCESS: {sum(1 for x in run_results if x['crawl_status'] == 'SUCCESS')}")
    print(
        f"NO_POST_24H: {sum(1 for x in run_results if x['crawl_status'] == 'NO_POST_24H')}"
    )
    print(
        f"CRAWL_ERROR: {sum(1 for x in run_results if x['crawl_status'] == 'CRAWL_ERROR')}"
    )

    try:
        save_results_to_json(run_results)
        print("Đã lưu kết quả vào file top_posts_results.json")
    except Exception as exc:
        print(f"Lỗi khi lưu JSON: {exc}")

    try:
        write_results_to_output_sheet(run_results)
        print(f"Đã ghi kết quả vào tab '{OUTPUT_WORKSHEET_NAME}' trong Google Sheet.")
    except Exception as exc:
        print(f"Lỗi khi ghi output sheet: {exc}")


if __name__ == "__main__":
    main()
