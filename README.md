# PowerNote AI

PowerNote AI is a personal AI voice diary for factual notes, nutrition tracking, and fitness logging. Send a voice or text message to the Telegram bot, and the service uses OpenAI to transcribe audio, extract factual events, detect food and workout logs, store entries locally, and answer semantic questions over your diary with embeddings and an LLM.

The project is designed as a self-hosted personal service. It runs with Docker Compose and includes an Ansible playbook for provisioning and deploying the full service to Ubuntu 24.04.

## Features

- Telegram bot as the main interface.
- Voice message and plain text input.
- Speech-to-text via OpenAI.
- Fact extraction via OpenAI with structured JSON output.
- Semantic question answering over diary entries using embeddings and an LLM.
- Nutrition detection with estimated calories, protein, fat, carbs, fiber, fruit/vegetable intake, added sugar, ultra-processed food score, and a daily quality score.
- Fitness detection with daily walk, cardio, strength, and activity health score tracking.
- Grafana dashboard for nutrition and fitness trends.
- User health profile with default targets and an interactive `/profile_setup` questionnaire.
- Explicit Telegram modes with buttons for `Diary`, `Nutrition`, `Fitness`, and `Search`.
- Auto-detection remains available when no mode is selected.
- Local file-based storage:
  - `data/diary.jsonl`
  - `data/embeddings.jsonl`
  - `data/nutrition.jsonl`
  - `data/fitness.jsonl`
  - `data/profile.json`
  - `data/failed_messages.jsonl`
- Bot commands:
  - `/start` - help message.
  - `/note <text>` - force-save text as a diary entry.
  - `/food <text>` - force-save text as a nutrition entry.
  - `/fitness <text>` - force-save text as a fitness entry.
  - `/last` - show the last 5 entries.
  - `/today` - show today's entries.
  - `/search <query>` - answer a question using diary content.
  - `/profile` - show nutrition and fitness profile settings.
  - `/profile_setup` - run a profile setup questionnaire.
  - `/nutrition_today` - show today's nutrition totals.
  - `/fitness_week` - show today's fitness progress.
  - `/undo_last` - delete the last saved diary, nutrition, or fitness entry.
  - `/cancel` - cancel search input mode.

## Project Structure

```text
.
├── ansible/
│   ├── inventories/
│   │   └── production.example.yml
│   ├── playbooks/
│   │   └── deploy.yml
│   ├── vpn-secrets.example.yml
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
│       ├── l2tp_vpn/
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
├── grafana/
│   ├── dashboards/
│   └── provisioning/
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

When no mode is selected, PowerNote AI still tries to auto-detect whether a message is food, fitness, or a regular diary note. Auto mode uses simple local keyword routing before asking the LLM, so obvious food or workout messages are less likely to be saved to the factual diary by mistake. Explicit mode selection is still the most reliable option.

When a message contains food, PowerNote AI replies with estimated calories, protein, fiber, the added meal score, and today's totals. Daily nutrition quality also accounts for calories, protein, fiber, fruit/vegetable intake, added sugar, and ultra-processed food. When a message contains a workout or physical activity, it replies with today's walk, cardio, strength, and activity health score.

If a voice transcription or automatic classification is wrong, use `/undo_last` or the `Delete Last` Telegram button. PowerNote AI removes every diary, nutrition, and fitness entry created from the latest Telegram message.

## Grafana Dashboard

The Docker Compose stack includes Grafana on port `65080`:

```text
http://YOUR_SERVER_IP:65080
```

Authentication is disabled intentionally for this personal VPS setup. The dashboard is provisioned automatically and appears as `PowerNote AI Health`.

The dashboard shows:

- daily nutrition calories vs limit;
- daily protein vs target;
- daily fiber vs target;
- daily nutrition quality score;
- daily fruit and vegetable intake vs target;
- daily added sugar vs limit;
- daily ultra-processed food score vs limit;
- daily activity minutes split into walk, cardio, and strength, with a daily target;
- daily activity health score.

Nutrition and activity are displayed daily so the dashboard stays focused on the current weight-loss workflow: food quality, calories, protein, fiber, fruit and vegetables, added sugar, ultra-processed food, and same-day movement.

Grafana reads PostgreSQL tables generated by the `metrics-sync` service. JSONL files remain the source of truth; metrics are recomputed from them every minute.

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

### 3. Prepare VPN Credentials

Create the ignored secrets file from its public template:

```bash
cp ansible/vpn-secrets.example.yml ansible/vpn-secrets.yml
```

Replace all placeholder values. Use a unique username, a long random password, and a random IPsec pre-shared key of at least 32 characters. For example, generate secrets with `openssl rand -hex 32`. The real `ansible/vpn-secrets.yml` is ignored by Git and must never be committed.

### 4. Run The Playbook

```bash
ansible-playbook -i ansible/inventories/production.yml ansible/playbooks/deploy.yml
```

The playbook performs a full server setup and deploy:

- configures SSH on port `65022` and verifies that the new port is reachable;
- installs Fail2ban and enables SSH protection: 5 failed attempts in 10 minutes, 24-hour ban;
- sends Telegram notifications when Fail2ban bans an IP;
- installs an L2TP/IPsec VPN using strongSwan, xl2tpd, and MSCHAPv2 authentication;
- enables VPN client forwarding and exposes UDP ports `500` and `4500` while rejecting unprotected L2TP traffic;
- installs Docker and the Docker Compose plugin;
- creates `/opt/powernote` and `/opt/powernote/data`;
- sets the data directory ownership to UID/GID `1000`, used by `appuser` inside the container;
- copies the project to the server;
- copies the local `.env` file to the server with `0600` permissions;
- rebuilds the Docker image when code or build files change;
- recreates the container from the new image;
- recreates the container without rebuilding when only `.env` changes;
- starts the existing image if there are no changes but the container is stopped.
- starts Grafana on port `65080` with anonymous read-only access.

### 5. Check The Server

```bash
ssh -p 65022 -i ~/.ssh/id_rsa root@YOUR_SERVER_IP
cd /opt/powernote
docker compose ps
docker compose logs -f powernote
```

### Keenetic VPN Client

Create an `L2TP/IPsec` connection in Keenetic with these fields:

- server address: the VPS public IP address;
- username and password: `l2tp_vpn_username` and `l2tp_vpn_password` from `ansible/vpn-secrets.yml`;
- IPsec pre-shared key: `l2tp_vpn_ipsec_psk` from the same file;
- authentication protocol: `MSCHAPv2`;
- obtain the client IP address and DNS automatically.

Do not configure plain L2TP without IPsec. The server accepts L2TP traffic only after IPsec protection has been established. VPN clients receive addresses from `10.99.0.10-10.99.0.20` and route internet traffic through the VPS.

## Storage Format

`data/diary.jsonl`:

```json
{"id":"82d4...","message_id":"telegram:123:456","occurred_at":"2026-06-23T22:14:00+02:00","created_at":"2026-06-23T22:15:08+02:00","facts":["The user talked to their mother."],"source":"voice","raw_text":"Today I talked to my mother..."}
```

Every stored entry has a stable `id`. `message_id` groups all records extracted from one Telegram message, `occurred_at` describes when the event happened, and `created_at` records when it was saved. This allows `Delete Last` to work correctly even for backdated entries.

`data/embeddings.jsonl` stores entry vectors separately from the main diary and links them by entry `id`. New entries are indexed when they are saved. Older entries are indexed automatically on the first `/search` request. Search first retrieves relevant entries by embeddings, then the LLM produces a direct answer using only the retrieved facts.

`data/failed_messages.jsonl` stores the original text only when OpenAI processing fails. Successfully processed messages are not duplicated in a separate transcript log.

`data/nutrition.jsonl`:

```json
{"id":"70c2...","message_id":"telegram:123:457","occurred_at":"2026-07-14T09:30:00+02:00","created_at":"2026-07-14T09:31:04+02:00","meal_name":"breakfast","items":["oatmeal","banana"],"calories_kcal":420,"protein_g":18,"fat_g":10,"carbs_g":65,"fiber_g":8,"fruit_veg_g":120,"added_sugar_g":0,"ultra_processed_score":10,"health_score":78,"score_reason":"Good fiber and moderate calories, but protein could be higher.","source":"text","raw_text":"I had oatmeal with a banana for breakfast."}
```

`data/fitness.jsonl`:

```json
{"id":"e498...","message_id":"telegram:123:458","occurred_at":"2026-07-14T19:00:00+02:00","created_at":"2026-07-14T19:01:22+02:00","activity_type":"cardio","duration_minutes":30,"intensity":"moderate","muscle_groups":["legs"],"estimated_calories_kcal":300,"effort_score":75,"score_reason":"Good cardio session for the current goal.","source":"voice","raw_text":"I ran for 30 minutes."}
```

Existing JSONL files using the previous `datetime` and `tags` format are migrated automatically on startup. Legacy text logs, the tag registry, and the full raw transcript log are removed after migration; diary embeddings are rebuilt automatically when search is next used.

`data/profile.json` stores the local health profile used for nutrition and fitness scoring. By default it assumes a 40-year-old person, 76 kg, 176 cm, desk job, with a goal to lose 5 kg and build muscle. Run `/profile_setup` in the bot to update it interactively.

## Security Notes

- Do not commit `.env`, real inventory files, or `ansible/vpn-secrets.yml`.
- Only `.env.example`, `ansible/inventories/production.example.yml`, and `ansible/vpn-secrets.example.yml` should be committed.
- Keep your working `.env` locally and on the server only.
- Set `ALLOWED_TELEGRAM_USER_IDS` for a private diary.
- The Ansible deployment configures SSH on port `65022` and installs Fail2ban with Telegram ban notifications.
- Grafana is exposed without authentication on port `65080`; use it only when this is acceptable for your VPS.
- The app stores diary data locally on your server as JSONL. PostgreSQL is used only for Grafana metrics aggregates.
- Nutrition and fitness scores are estimates for personal tracking. They are not medical advice.

## Language Notes

The default bot prompts and messages are currently optimized for Russian-language personal notes. The architecture is language-agnostic, but for another language you may want to adjust prompts, button labels, and bot response text in `src/powernote/ai.py` and `src/powernote/bot.py`.
