# Telegram Support Bot

Простой, но мощный Telegram-бот для организации поддержки пользователей через администраторскую группу. Написан на `aiogram 3.x`.

Бот позволяет пользователям отправлять сообщения в личный чат с ботом, которые затем пересылаются в специальную группу для администраторов. Администраторы могут отвечать на эти сообщения прямо в группе, а бот пересылает их ответы обратно соответствующему пользователю.

## 🚀 Возможности

### Для пользователей

- Просто отправьте сообщение боту, и оно будет доставлено администраторам.
- Получайте ответы от администраторов прямо в личный чат.

### Для администраторов

- **Ответы на сообщения**: Просто ответьте на пересланное от пользователя сообщение в группе, и ваш ответ будет отправлен ему.
- **Информация о пользователе**: Команда `/who` (в ответ на сообщение или с ID) покажет информацию о пользователе (ID, юзернейм, дата первого запуска).
- **Статистика**: Команда `/stats` покажет общую статистику по боту (количество пользователей, активность, общее число сообщений).
- **Управление блокировками**:
    - `/ban <ID или ответ> [срок] [причина]` — заблокировать пользователя. Срок указывается в формате `1d` (день), `2w` (недели), `3m` (минуты), `4h` (часы). Если срок не указан, бан будет вечным.
    - `/unban <ID или ответ>` — разблокировать пользователя.
    - `/banlist` — посмотреть список заблокированных пользователей с пагинацией.
- **Прямое сообщение**: Команда `/msg <ID> <текст>` позволяет отправить сообщение пользователю от имени бота.

## 📦 Установка и запуск

1.  **Клонируйте репозиторий:**
    ```sh
    cd /opt
    git clone https://github.com/Mythological/telegram-support-bot
    cd support-bot
    ```

2.  **Создайте и активируйте виртуальное окружение:**
    ```sh
    python -m venv venv
    source venv/bin/activate  # Для Windows: venv\Scripts\activate
    ```

3.  **Установите зависимости:**
    ```sh
    pip install -r requirements.txt
    ```

4.  **Настройте переменные окружения:**

    Создайте файл `.env` в корне проекта, скопировав `.env.example`:
    ```sh
    cp .env.example .env
    ```

    Откройте файл `.env` и впишите ваши данные:
    - `BOT_TOKEN`: Токен вашего Telegram-бота, полученный от [@BotFather](https://t.me/BotFather).
    - `ADMIN_CHAT_ID`: ID вашей администраторской группы. ID группы должен быть числом (для групп он обычно начинается с `-100...`).

    > **Как узнать ID группы?** Добавьте в группу бота [@userinfobot](https://t.me/userinfobot), и он покажет ID чата.

5.  **Запустите бота в ручную:**
    ```sh
    python main.py
    ```

6. **Запустите бота автоматически через systemd:**
    - Создайте юзера и дайте ему права:
      ```sh
       sudo useradd -r -s /bin/bash -m -d /home/telegram_bot telegram_bot
       sudo chown -R telegram_bot:telegram_bot /opt/telegram-support-bot
       sudo chmod 640 /opt/telegram-support-bot/.env
      ```
     - Если после запуска бота возникнут ошибки на созданную папку ботом
      ```sh
       sudo chown -R telegram_bot:telegram_bot /opt/telegram-support-bot/meta
       sudo chmod 755 /opt/telegram-support-bot/meta
      ```
      -
      ```sh
       sudo nano /etc/systemd/system/mybot.service
      ```

```sh
[Unit]
Description=Telegram Support Bot (BlaBlaBla)
After=network.target

[Service]
User=telegram_bot
Group=telegram_bot
WorkingDirectory=/opt/telegram-support-bot
EnvironmentFile=/opt/telegram-support-bot/.env
ExecStart=/opt/telegram-support-bot/venv/bin/python /opt/telegram-support-bot/main.py
Restart=always
RestartSec=10

[Install]
WantedBy=multi-user.target
```
- Запуск и автозагрузка
```sh
sudo systemctl daemon-reload
sudo systemctl enable mybot.service
sudo systemctl start mybot.service
sudo systemctl status mybot.service
```

-- логи

```sh
sudo journalctl -u mybot.service -n 50

```

## 📁 Структура файлов

- `main.py`: Основной файл с логикой бота.
- `requirements.txt`: Список необходимых Python-библиотек.
- `.env`: Файл с вашими секретными ключами.
- `meta/`: Директория, где бот хранит свои данные:
    - `users_data.json`: База данных пользователей.
    - `messages_mapping.json`: Связь между сообщениями пользователей и администраторов.
    - `admin_log.txt`: Логи действий администраторов.
