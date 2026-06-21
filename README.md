# Telegram music bot

Telegram-бот принимает название песни или ссылку, ищет доступный аудио-результат через `yt-dlp`, скачивает аудио в MP3 и отправляет его в чат.

Поддерживается:

- поиск по названию через YouTube и SoundCloud;
- прямые ссылки на источники, которые поддерживает `yt-dlp` — например YouTube, SoundCloud, Bandcamp;
- ссылки Spotify и Yandex Music как источник названия трека: бот не обходит DRM и не скачивает закрытые каталоги напрямую, а ищет доступную аудио-версию по найденному названию.

Используйте бота только для контента, на который у вас есть права или разрешение.

## Запуск

1. Создайте бота через [@BotFather](https://t.me/BotFather) и получите токен.
2. Установите Python 3.11+ и `ffmpeg`.
3. Установите зависимости:

```bash
python -m venv .venv
source .venv/bin/activate
pip install -r requirements.txt
```

4. Передайте токен и запустите:

```bash
export TELEGRAM_BOT_TOKEN="123456:token"
python bot.py
```

## Настройки

Можно задать переменные окружения:

- `TELEGRAM_BOT_TOKEN` — токен Telegram-бота, обязательно.
- `DOWNLOAD_DIR` — директория временных загрузок, по умолчанию `downloads`.
- `MAX_FILE_MB` — максимальный размер отправляемого файла, по умолчанию `45`.
- `SEARCH_RESULTS` — сколько результатов проверять в каждом источнике, по умолчанию `5`.
- `TELEGRAM_TIMEOUT` — таймаут подключения к Telegram API в секундах, по умолчанию `30`.
- `TELEGRAM_PROXY_URL` — HTTP/SOCKS-прокси для Telegram API, если Telegram недоступен из вашей сети.

## Termux: частые ошибки

Если бот падает с `telegram.error.TimedOut`, Python не может подключиться к `api.telegram.org`. Проверьте сеть:

```bash
curl -I https://api.telegram.org
```

Если `curl` тоже зависает или падает по таймауту, включите VPN или задайте прокси:

```bash
export TELEGRAM_PROXY_URL="http://127.0.0.1:8080"
export TELEGRAM_TIMEOUT=60
python bot.py
```

Если вы случайно показали токен кому-то ещё, перевыпустите его в `@BotFather` командой `/revoke`.
