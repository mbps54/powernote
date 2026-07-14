# PowerNote AI

PowerNote AI is a personal AI voice diary for factual notes, nutrition tracking, and fitness logging. Send a voice or text message to the Telegram bot, and the service uses OpenAI to transcribe audio, extract factual events, detect food and workout logs, store entries locally, and answer semantic questions over your diary with embeddings and an LLM.

The project is designed as a self-hosted personal service. It runs with Docker Compose and includes an Ansible playbook for provisioning and deploying the full service to Ubuntu 24.04.

## Features

- Telegram bot as the main interface.
- Voice message and plain text input.
- Speech-to-text via OpenAI.
- Fact and tag extraction via OpenAI with structured JSON output.
- Semantic question answering over diary entries using embeddings and an LLM.
- Nutrition detection with estimated calories, protein, fat, carbs, fiber, and a health score.
- Fitness detection with weekly progress, effort score, and success percentage against profile-based targets.
- User health profile with default targets and an interactive `/profile_setup` questionnaire.
- Explicit Telegram modes with buttons for `Diary`, `Nutrition`, `Fitness`, and `Search`.
- Auto-detection remains available when no mode is selected.
- Local file-based storage:
  - `data/diary.log`
  - `data/diary.jsonl`
  - `data/embeddings.jsonl`
  - `data/nutrition.log`
  - `data/nutrition.jsonl`
  - `data/fitness.log`
  - `data/fitness.jsonl`
  - `data/profile.json`
  - `data/tags.json`
  - `data/raw_transcripts.log`
- Bot commands:
  - `/start` - help message.
  - `/note <text>` - force-save text as a diary entry.
  - `/food <text>` - force-save text as a nutrition entry.
  - `/fitness <text>` - force-save text as a fitness entry.
  - `/last` - show the last 5 entries.
  - `/today` - show today's entries.
  - `/tags` - list known tags.
  - `/tag <tag>` - show entries by tag.
  - `/search <query>` - answer a question using diary content.
  - `/profile` - show nutrition and fitness profile settings.
  - `/profile_setup` - run a profile setup questionnaire.
  - `/nutrition_today` - show today's nutrition totals.
  - `/fitness_week` - show this week's fitness progress.
  - `/cancel` - cancel search input mode.

## Project Structure

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

## Configuration

Create a local environment file from the example:

```bash
cp .env.example .env
```

Fill in your own values:

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

Where to get the required values:

- `TELEGRAM_BOT_TOKEN`: open `@BotFather` in Telegram, run `/newbot`, and copy the token it gives you.
- `OPENAI_API_KEY`: create an API key in the OpenAI Platform at <https://platform.openai.com/api-keys>. Make sure billing is enabled at <https://platform.openai.com/settings/organization/billing/overview>.
- `ALLOWED_TELEGRAM_USER_IDS`: your numeric Telegram user ID. You can get it from Telegram helper bots that show your user ID, or by inspecting Telegram Bot API updates after sending a message to your bot.

If `ALLOWED_TELEGRAM_USER_IDS` is empty, the bot is open to anyone who can message it. For a private diary, always set it.

## Local Run With Docker Compose

Build and start the service:

```bash
docker compose up -d --build
```

Follow logs:

```bash
docker compose logs -f powernote
```

Stop the service:

```bash
docker compose down
```

Data is stored in the local `./data` directory.

Use the Telegram mode buttons when category accuracy matters:

- `Diary` makes the next text or voice message go to the factual diary.
- `Nutrition` makes the next text or voice message go to `nutrition.jsonl`.
- `Fitness` makes the next text or voice message go to `fitness.jsonl`.
- `Search` asks a semantic question over diary entries.

You can also use commands: `/note`, `/food`, and `/fitness`.

When no mode is selected, PowerNote AI still tries to auto-detect whether a message is food, fitness, or a regular diary note. Explicit mode selection is more reliable.

When a message contains food, PowerNote AI replies with estimated calories, protein, fat, carbs, fiber, the added health score, and today's totals. When a message contains a workout or physical activity, it replies with the added activity plus this week's progress and success percentage.

## Remote Deploy To Ubuntu 24.04 With Ansible

Control machine requirements:

- Ansible installed.
- SSH access to the server as `root`.
- SSH key authentication.
- Target server running Ubuntu 24.04.

### 1. Configure YAML Inventory

Create a working inventory from the example:

```bash
cp ansible/inventories/production.example.yml ansible/inventories/production.yml
```

Edit `ansible/inventories/production.yml`:

```yaml
all:
  children:
    powernote:
      hosts:
        powernote-prod:
          ansible_host: YOUR_SERVER_IP
          ansible_user: root
          ansible_port: 65022
          ansible_ssh_private_key_file: ~/.ssh/id_rsa
```

Replace `YOUR_SERVER_IP` with your VPS public IP address.

The `65022` SSH port is intentional. The Ansible `security` role moves SSH away from the default port `22` to reduce background internet noise from automated scanners. After the first successful deploy, keep using `65022` in your inventory and SSH commands.

### 2. Prepare Service Configuration

Create `.env` from the example and fill in your values:

```bash
cp .env.example .env
```

The same `.env` file is used for both local Docker Compose and remote Ansible deployment. During deployment, Ansible copies it to `/opt/powernote/.env` on the server with `0600` permissions.

### 3. Run The Playbook

```bash
ansible-playbook -i ansible/inventories/production.yml ansible/playbooks/deploy.yml
```

The playbook performs a full server setup and deploy:

- configures SSH on port `65022` and verifies that the new port is reachable;
- installs Fail2ban and enables SSH protection: 5 failed attempts in 10 minutes, 24-hour ban;
- sends Telegram notifications when Fail2ban bans an IP;
- installs Docker and the Docker Compose plugin;
- creates `/opt/powernote` and `/opt/powernote/data`;
- sets the data directory ownership to UID/GID `1000`, used by `appuser` inside the container;
- copies the project to the server;
- copies the local `.env` file to the server with `0600` permissions;
- rebuilds the Docker image when code or build files change;
- recreates the container from the new image;
- recreates the container without rebuilding when only `.env` changes;
- starts the existing image if there are no changes but the container is stopped.

### 4. Check The Server

```bash
ssh -p 65022 -i ~/.ssh/id_rsa root@YOUR_SERVER_IP
cd /opt/powernote
docker compose ps
docker compose logs -f powernote
```

## Storage Format

`data/diary.log`:

```text
2026-06-23 22:14 [family]
The user talked to their mother. The mother said she would arrive on Saturday.
```

`data/diary.jsonl`:

```json
{"datetime":"2026-06-23T22:14:00+02:00","tags":["family"],"facts":["The user talked to their mother."],"source":"voice","raw_text":"Today I talked to my mother..."}
```

`data/tags.json`:

```json
{"tags":["family","car","prices","finance","work","health","documents","shopping"]}
```

`data/embeddings.jsonl` stores entry vectors separately from the main diary. New entries are indexed when they are saved. Older entries are indexed automatically on the first `/search` request. Search first retrieves relevant entries by embeddings, then the LLM produces a direct answer using only the retrieved facts.

`data/raw_transcripts.log` stores raw text transcripts before fact extraction. This helps preserve input when the OpenAI API is temporarily unavailable.

`data/nutrition.jsonl`:

```json
{"datetime":"2026-07-14T09:30:00+02:00","meal_name":"breakfast","items":["oatmeal","banana"],"calories_kcal":420,"protein_g":18,"fat_g":10,"carbs_g":65,"fiber_g":8,"health_score":78,"score_reason":"Good fiber and moderate calories, but protein could be higher.","source":"text","raw_text":"I had oatmeal with a banana for breakfast."}
```

`data/fitness.jsonl`:

```json
{"datetime":"2026-07-14T19:00:00+02:00","activity_type":"run","duration_minutes":30,"intensity":"moderate","muscle_groups":["legs"],"estimated_calories_kcal":300,"effort_score":75,"score_reason":"Good cardio session for the current goal.","source":"voice","raw_text":"I ran for 30 minutes."}
```

`data/profile.json` stores the local health profile used for nutrition and fitness scoring. By default it assumes a 40-year-old person, 76 kg, 176 cm, desk job, with a goal to lose 5 kg and build muscle. Run `/profile_setup` in the bot to update it interactively.

## Security Notes

- Do not commit `.env` or real inventory files.
- Only `.env.example` and `ansible/inventories/production.example.yml` should be committed.
- Keep your working `.env` locally and on the server only.
- Set `ALLOWED_TELEGRAM_USER_IDS` for a private diary.
- The Ansible deployment configures SSH on port `65022` and installs Fail2ban with Telegram ban notifications.
- The app stores diary data locally on your server. No database or external storage service is required.
- Nutrition and fitness scores are estimates for personal tracking. They are not medical advice.

## Language Notes

The default bot prompts and messages are currently optimized for Russian-language personal notes. The architecture is language-agnostic, but for another language you may want to adjust prompts, button labels, and bot response text in `src/powernote/ai.py` and `src/powernote/bot.py`.
