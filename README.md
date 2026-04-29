# AI_Automatic

pip install -r requirements.txt.
<!-- Cách lấy api gg sheets -->
https://console.cloud.google.com
creds.json
Google Sheets API
Google Drive API
https://www.facebook.com/groups/168876233173374

FB Groups Input
input_groups
crawl_output

<!-- INPUT -->
group_url
<!-- OUTPUT -->
crawl_time	group_url	group_title	crawl_status	error_message	posts_fetched	has_top_post_24h	user	time_vn	url	likesCount	commentsCount	sharesCount	engagement	text

<!-- CHẠY LOCAL uvicorn api_server:app --reload -->

<!-- File .env -->
APIFY_TOKEN=
TELEGRAM_BOT_TOKEN=
TELEGRAM_CHAT_ID=
GOOGLE_CREDS_FILE=creds.json
SPREADSHEET_NAME=FB Groups Input
INPUT_WORKSHEET_NAME=input_groups
OUTPUT_WORKSHEET_NAME=crawl_output
GROUP_URL_COLUMN=group_url

RESULTS_AMOUNT=20
LOOKBACK_DAYS=1
MIN_DELAY_SECONDS=4
MAX_DELAY_SECONDS=9
