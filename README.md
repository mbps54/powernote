# Powernote

MVP-сервис голосового дневника фактов. Пользователь отправляет голосовое или текстовое сообщение в Telegram-бота, сервис расшифровывает голос, извлекает только факты, присваивает теги и сохраняет записи в локальные файлы.

## Возможности

- Telegram-бот как основной интерфейс.
- Прием voice message и обычного текста.
- Speech-to-text через OpenAI.
- Извлечение фактов и тегов через OpenAI в строгий JSON.
- Ответы на вопросы по дневнику через embeddings и LLM.
- Постоянная кнопка `Поиск` для быстрого ввода вопроса.
- Хранение MVP в локальных файлах:
  - `data/diary.log`
  - `data/diary.jsonl`
  - `data/embeddings.jsonl`
  - `data/tags.json`
  - `data/raw_transcripts.log`
- Команды:
  - `/start` - помощь.
  - `/last` - последние 5 записей.
  - `/today` - записи за сегодня.
  - `/tags` - список тегов.
  - `/tag <tag>` - последние записи по тегу.
  - `/search <query>` - ответ на вопрос по содержимому дневника.
  - `/cancel` - отмена ввода вопроса после нажатия кнопки поиска.

## Структура проекта

```text
.
├── ansible/
│   ├── inventories/
│   │   └── production.example.yml
│   ├── playbooks/
│   │   └── deploy.yml
│   └── roles/
│       ├── security/
│       │   ├── defaults/
│       │   ├── handlers/
│       │   ├── tasks/
│       │   └── templates/
│       ├── fail2ban/
│       │   ├── defaults/
│       │   ├── handlers/
│       │   ├── tasks/
│       │   └── templates/
│       └── powernote/
│           ├── defaults/
│           │   └── main.yml
│           ├── handlers/
│           │   └── main.yml
│           └── tasks/
│               └── main.yml
├── data/
│   └── .gitkeep
├── src/
│   └── powernote/
│       ├── ai.py
│       ├── bot.py
│       ├── config.py
│       ├── main.py
│       ├── models.py
│       └── storage.py
├── .env.example
├── .dockerignore
├── ansible.cfg
├── docker-compose.yml
├── Dockerfile
└── pyproject.toml
```

## Локальный запуск через Docker Compose

1. Создайте конфигурационный файл из примера:

```bash
cp .env.example .env
```

2. Создайте Telegram-бота через `@BotFather` и получите токен.
3. Получите OpenAI API key.
4. Узнайте свой Telegram user ID.
5. Заполните `.env` своими значениями:

```env
TELEGRAM_BOT_TOKEN=replace-with-your-telegram-bot-token
OPENAI_API_KEY=replace-with-your-openai-api-key
OPENAI_TRANSCRIBE_MODEL=gpt-4o-mini-transcribe
OPENAI_FACT_MODEL=gpt-4.1-mini
OPENAI_EMBEDDING_MODEL=text-embedding-3-small
SEMANTIC_SEARCH_LIMIT=5
SEMANTIC_SEARCH_MIN_SCORE=0.25
APP_TIMEZONE=Europe/Berlin
DATA_DIR=/app/data
LOG_LEVEL=INFO
ALLOWED_TELEGRAM_USER_IDS=replace-with-your-telegram-user-id
```

Где взять значения:

- `TELEGRAM_BOT_TOKEN`: в Telegram откройте `@BotFather`, выполните `/newbot` и скопируйте выданный token.
- `OPENAI_API_KEY`: создайте ключ в OpenAI Platform: <https://platform.openai.com/api-keys>. Для работы ключа также должен быть включен billing: <https://platform.openai.com/settings/organization/billing/overview>.
- `ALLOWED_TELEGRAM_USER_IDS`: это ваш numeric Telegram user ID. Его можно узнать через специальных Telegram-ботов для показа user ID, например отправив им `/start`, либо через Telegram Bot API после сообщения вашему боту.

6. Запустите сервис:

```bash
docker compose up -d --build
```

7. Посмотреть логи:

```bash
docker compose logs -f powernote
```

8. Остановить сервис:

```bash
docker compose down
```

Данные сохраняются в локальной директории `./data`.

## Запуск на удаленном Ubuntu 24.04 через Ansible

Требования к управляющей машине:

- установлен Ansible;
- есть SSH-доступ на сервер пользователем `root`;
- вход выполняется по SSH-ключу;
- сервер: Ubuntu 24.04.

### 1. Настройте inventory в YAML

Создайте рабочий inventory из примера:

```bash
cp ansible/inventories/production.example.yml ansible/inventories/production.yml
```

Отредактируйте `ansible/inventories/production.yml`:

```yaml
all:
  children:
    powernote:
      hosts:
        powernote-prod:
          ansible_host: 203.0.113.10
          ansible_user: root
          ansible_port: 65022
          ansible_ssh_private_key_file: ~/.ssh/id_rsa
```

### 2. Подготовьте конфигурацию сервиса

Создайте `.env` из примера и заполните своими значениями:

```bash
cp .env.example .env
```

```env
TELEGRAM_BOT_TOKEN=replace-with-your-telegram-bot-token
OPENAI_API_KEY=replace-with-your-openai-api-key
OPENAI_TRANSCRIBE_MODEL=gpt-4o-mini-transcribe
OPENAI_FACT_MODEL=gpt-4.1-mini
OPENAI_EMBEDDING_MODEL=text-embedding-3-small
SEMANTIC_SEARCH_LIMIT=5
SEMANTIC_SEARCH_MIN_SCORE=0.25
APP_TIMEZONE=Europe/Berlin
DATA_DIR=/app/data
LOG_LEVEL=INFO
ALLOWED_TELEGRAM_USER_IDS=replace-with-your-telegram-user-id
```

Значения берутся так же, как в локальном запуске: Telegram token выдает `@BotFather`, OpenAI API key создается на <https://platform.openai.com/api-keys>, Telegram user ID нужен для ограничения доступа к личному дневнику.

### 3. Запустите один playbook

```bash
ansible-playbook -i ansible/inventories/production.yml ansible/playbooks/deploy.yml
```

Playbook выполнит полный запуск сервиса:

- настроит SSH на порту `65022` и проверит доступность нового порта;
- установит Fail2ban и включит защиту SSH: 5 попыток за 10 минут, блокировка на 24 часа;
- отправит в Telegram уведомление при блокировке IP;
- установит Docker и Docker Compose plugin;
- создаст директории `/opt/powernote` и `/opt/powernote/data`;
- назначит каталогу данных UID/GID `1000`, используемые `appuser` внутри контейнера;
- скопирует проект на сервер;
- скопирует локальный `.env` на сервер с правами `0600`;
- при изменении кода или файлов сборки соберет новый Docker image;
- пересоздаст контейнер из нового image;
- при изменении только `.env` пересоздаст контейнер без сборки image;
- если изменений нет, но контейнер остановлен, запустит существующий image.

### 4. Проверка на сервере

```bash
ssh -p 65022 -i ~/.ssh/id_rsa root@203.0.113.10
cd /opt/powernote
docker compose ps
docker compose logs -f powernote
```

## Формат хранения

`data/diary.log`:

```text
2026-06-23 22:14 [семья]
Пользователь разговаривал с мамой. Мама сказала, что приедет в субботу.
```

`data/diary.jsonl`:

```json
{"datetime":"2026-06-23T22:14:00+02:00","tags":["семья"],"facts":["Пользователь разговаривал с мамой."],"source":"voice","raw_text":"Сегодня разговаривал с мамой..."}
```

`data/tags.json`:

```json
{"tags":["семья","машина","цены","финансы","работа","здоровье","документы","покупки"]}
```

`data/embeddings.jsonl` хранит векторы записей отдельно от основного дневника. Новые записи индексируются при сохранении. Старые записи автоматически индексируются при первом вызове `/search`. Команда сначала выбирает релевантные записи по embeddings, затем LLM формирует прямой ответ на вопрос только на основе найденных фактов.

## Безопасность

- Не коммитьте `.env` и реальные inventory-файлы.
- В git должен попадать только `.env.example`; рабочий `.env` храните локально и на сервере.
- `.env` является единственным источником настроек приложения для локального и удаленного запуска.
- Для личного бота заполните `ALLOWED_TELEGRAM_USER_IDS` списком Telegram user ID через запятую.
