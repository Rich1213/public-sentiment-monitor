import os
import requests
from dotenv import load_dotenv
from src.utils.logger import get_logger

load_dotenv()

logger = get_logger(__name__)


class TelegramNotifier:
    def __init__(self):
        self.bot_token = os.getenv("TELEGRAM_BOT_TOKEN")
        self.chat_id   = os.getenv("TELEGRAM_CHAT_ID")

        if not self.bot_token or not self.chat_id:
            raise ValueError("TELEGRAM_BOT_TOKEN or TELEGRAM_CHAT_ID not found in environment variables.")

        self.api_url = f"https://api.telegram.org/bot{self.bot_token}/sendMessage"

    def send_sentiment_alert(self, keyword: str, article: dict, analysis: dict):
        """發送單篇高強度負面警報到 Telegram。"""
        sentiment_map = {"正面": "✅", "中立": "⚪", "負面": "🚨"}
        emoji = sentiment_map.get(analysis["sentiment"], "❓")

        message = (
            f"{emoji} **輿論監測警報: {keyword}**\n\n"
            f"📌 **標題**: {article['title']}\n"
            f"📊 **情緒**: {analysis['sentiment']} (得分: {analysis['score']})\n"
            f"🏷️ **主題**: {analysis['theme']}\n"
            f"💡 **分析**: {analysis['reason']}\n\n"
            f"🔗 [閱讀原文]({article['link']})"
        )

        payload = {
            "chat_id":    self.chat_id,
            "text":       message,
            "parse_mode": "Markdown",
        }

        try:
            response = requests.post(self.api_url, json=payload, timeout=10)
            response.raise_for_status()
            logger.info("Telegram 警報發送成功：%s", keyword)
        except Exception as e:
            logger.error("Telegram 警報發送失敗：%s", e)

    def send_daily_report(self, report_text: str) -> bool:
        """
        發送輿情日報摘要到 Telegram。
        Telegram 訊息上限 4096 字元，超過時依段落切分發送。
        """
        MAX_LEN = 4000
        chunks: list[str] = []

        if len(report_text) <= MAX_LEN:
            chunks = [report_text]
        else:
            lines       = report_text.split("\n")
            current: list[str] = []
            current_len = 0
            for line in lines:
                if current_len + len(line) + 1 > MAX_LEN:
                    chunks.append("\n".join(current))
                    current     = [line]
                    current_len = len(line)
                else:
                    current.append(line)
                    current_len += len(line) + 1
            if current:
                chunks.append("\n".join(current))

        success = True
        for i, chunk in enumerate(chunks, 1):
            payload = {
                "chat_id":                  self.chat_id,
                "text":                     chunk,
                "parse_mode":               "Markdown",
                "disable_web_page_preview": True,
            }
            try:
                resp = requests.post(self.api_url, json=payload, timeout=10)
                resp.raise_for_status()
                logger.info("Telegram 日報第 %d/%d 段發送成功", i, len(chunks))
            except Exception as e:
                logger.error("Telegram 日報第 %d 段發送失敗：%s", i, e)
                success = False

        return success


if __name__ == "__main__":
    try:
        notifier    = TelegramNotifier()
        test_article  = {"title": "7-ELEVEN 食安問題引發網友討論", "link": "https://example.com"}
        test_analysis = {"sentiment": "負面", "score": 0.85, "theme": "食安", "reason": "測試警報通知"}
        notifier.send_sentiment_alert("7-ELEVEN", test_article, test_analysis)
        logger.info("Telegram 測試通知發送成功")
    except Exception as e:
        logger.error("測試失敗：%s", e)
