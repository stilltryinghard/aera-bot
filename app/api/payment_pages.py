from html import escape
from fastapi import APIRouter
from fastapi.responses import HTMLResponse
from app.config import get_settings
router = APIRouter()

def page(title: str, message: str) -> HTMLResponse:
    username = get_settings().bot_username.lstrip("@")
    if not username or not username.replace("_", "").isalnum():
        username = "AERAVPN_BOT"
    html = f"""<!doctype html><html lang="ru"><meta charset="utf-8">
<meta name="viewport" content="width=device-width,initial-scale=1"><title>AERA VPN</title>
<style>body{{background:#080b10;color:#f5f7fa;font:18px system-ui;display:grid;
place-items:center;min-height:95vh;margin:0}}main{{max-width:550px;padding:32px}}
h1{{color:#4ed8ff}}p{{line-height:1.6;color:#aeb9c9}}a{{display:inline-block;
background:#4ed8ff;color:#080b10;padding:14px 22px;border-radius:12px;text-decoration:none}}
</style><main><h1>{escape(title)}</h1><p>{escape(message)}</p>
<a href="https://t.me/{escape(username, quote=True)}">Открыть AERA в Telegram</a></main></html>"""
    return HTMLResponse(
        html,
        headers={
            "Cache-Control": "no-store",
            "Referrer-Policy": "no-referrer",
            "X-Content-Type-Options": "nosniff",
            "Content-Security-Policy": "default-src 'none'; style-src 'unsafe-inline'; "
            "base-uri 'none'; frame-ancestors 'none'; form-action 'none'",
        },
    )


@router.get("/payments/success", response_class=HTMLResponse)
async def success():
    return page(
        "Проверяем оплату",
        "Вернитесь в бот. После подтверждения платежа "
        "доступ появится в разделе «Моя подписка». Эта страница не подтверждает оплату.",
    )


@router.get("/payments/failure", response_class=HTMLResponse)
async def failure():
    return page(
        "Оплата не завершена",
        "Вернитесь в бот и проверьте статус заказа. "
        "Если деньги списались, не платите повторно: обратитесь в поддержку.",
    )


@router.get("/", response_class=HTMLResponse)
async def home():
    return page("AERA VPN", "Управление подпиской, тарифы и поддержка доступны в Telegram-боте.")
