# FilmSite – Complete Setup Guide / සම්පූර්ණ ස්ථාපන මාර්ගෝපදේශය

> This guide is written in **both Sinhala (සිංහල) and English** so you can follow along in your preferred language.

---

## Section 1: Telegram Setup / ටෙලිග්‍රාම් සැකසීම

### 1.1 Get API ID + API Hash / API ID සහ API Hash ලබා ගන්න

**English:**
1. Go to https://my.telegram.org in your browser.
2. Log in with your Telegram phone number (the one that owns the channel).
3. Click **"API development tools"**.
4. Fill in the form:
   - **App title**: `FilmSiteBot` (anything)
   - **Short name**: `filmsitebot`
   - **Platform**: `Other`
5. Click **"Create Application"**.
6. Copy the `api_id` (a number like `12345678`) and `api_hash` (a long hex string).
7. Save them in your `bot/.env` file:
   ```
   API_ID=12345678
   API_HASH=abcdef1234567890abcdef1234567890
   ```

**සිංහල:**
1. ඔබේ browser එකේ https://my.telegram.org යන්න.
2. ඔබේ Telegram phone number එකෙන් log in කරන්න (channel owner number).
3. **"API development tools"** click කරන්න.
4. Form එක fill කරන්න:
   - **App title**: `FilmSiteBot`
   - **Short name**: `filmsitebot`
   - **Platform**: `Other`
5. **"Create Application"** click කරන්න.
6. `api_id` (number) සහ `api_hash` (hex string) copy කරගන්න.
7. `bot/.env` file එකේ save කරන්න.

---

### 1.2 Create Bot with @BotFather / Bot හදන්නේ කෙසේද

**English:**
1. Open Telegram and search for **@BotFather**.
2. Send `/newbot`.
3. Enter a display name: `Film Site`
4. Enter a username (must end in `bot`): `yourfilmsitebot`
5. BotFather will give you a **Bot Token** like `7123456789:AAF...xyz`.
6. Save it in `bot/.env`:
   ```
   BOT_TOKEN=7123456789:AAFxxxxxxxxxxxxxxxxxxxxxxxxxxxxx
   ```

**සිංහල:**
1. Telegram open කර **@BotFather** search කරන්න.
2. `/newbot` send කරන්න.
3. Bot name දෙන්න: `Film Site`
4. Username දෙන්න (bot වලින් end වෙන්න ඕනෑ): `yourfilmsitebot`
5. BotFather ඔබට **Bot Token** එකක් දෙයි.
6. `bot/.env` file එකේ save කරන්න.

---

### 1.3 Get Channel IDs / Channel ID ලබා ගන්න

**English:**
1. Forward a message from your private channel to **@userinfobot** or **@RawDataBot**.
2. It will show the channel ID (usually starts with `-100`).
3. Example: `-1001234567890`
4. Save it:
   ```
   CHANNEL_ID=-1001234567890
   ```

**සිංහල:**
1. ඔබේ private channel එකෙන් message එකක් **@userinfobot** ට forward කරන්න.
2. Channel ID (generally `-100` වලින් start වෙයි) show වෙයි.
3. `bot/.env` file එකේ save කරන්න.

---

### 1.4 Generate Pyrogram Session File / Session File හදන්නේ කෙසේද

**English:**
1. On your VPS, run:
   ```bash
   pip install pyrogram tgcrypto
   python3 -c "
   from pyrogram import Client
   app = Client('myaccount')
   app.run()
   "
   ```
2. Enter your phone number, OTP, and 2FA password if asked.
3. A file `myaccount.session` will be created. **This file = your Telegram account. Never share it!**
4. Rename it and put it in the `bot/` folder.

**සිංහල:**
1. VPS එකේ ඉහත commands run කරන්න.
2. Phone number, OTP, සහ 2FA password (ඇත්නම්) enter කරන්න.
3. `myaccount.session` file එකක් හැදෙයි. **මේ file ටෙලිග්‍රාම් account හා සමාන. කිසිවෙකුට දෙන්නේ නැත්.**
4. Rename කර `bot/` folder එකට දාන්න.

---

## Section 2: TMDB API Key / TMDB API Key ලබා ගැනීම

**English:**
1. Go to https://www.themoviedb.org and create a free account.
2. Go to **Settings → API → Create → Developer**.
3. Fill in the application details (site URL, description).
4. Copy your **API Key (v3 auth)** (looks like: `a1b2c3d4e5f6...`).
5. Add to `bot/.env`:
   ```
   TMDB_API_KEY=a1b2c3d4e5f6789012345678901234ab
   ```

**සිංහල:**
1. https://www.themoviedb.org හි free account හදන්න.
2. **Settings → API → Create → Developer** යන්න.
3. Application details fill කරන්න.
4. **API Key (v3 auth)** copy කරගන්න.
5. `bot/.env` file එකට add කරන්න.

---

## Section 3: GitHub Repository Setup / GitHub Repository සැකසීම

**English:**
1. Go to https://github.com and create a new **private** repository named `film-web-site`.
2. Push your local code:
   ```bash
   git init
   git add .
   git commit -m "Initial commit"
   git remote add origin https://github.com/YOUR_USERNAME/film-web-site.git
   git push -u origin main
   ```
3. **Add Secrets** (Settings → Secrets and variables → Actions → New repository secret):
   | Secret name          | Value                                |
   |----------------------|--------------------------------------|
   | `CF_API_TOKEN`       | Cloudflare API Token (see Section 5) |
   | `CF_ACCOUNT_ID`      | Cloudflare Account ID                |
   | `TG_BOT_TOKEN`       | Your Telegram bot token              |
   | `TG_NOTIFY_CHAT_ID`  | Your Telegram chat ID for alerts     |

4. **Add Variables** (Settings → Secrets and variables → Actions → Variables):
   | Variable name     | Value                     |
   |-------------------|---------------------------|
   | `SITE_BASE_URL`   | `https://yoursite.lk`     |

5. **Get GitHub Personal Access Token** (for the bot to push to GitHub):
   - Go to https://github.com/settings/tokens → Generate new token (classic)
   - Scopes: `repo` (full control)
   - Copy the token and save in `bot/.env`:
     ```
     GITHUB_TOKEN=ghp_xxxxxxxxxxxxxxxxxxxx
     GITHUB_REPO=YOUR_USERNAME/film-web-site
     ```

**සිංහල:**
1. GitHub හි `film-web-site` නමින් **private** repository හදන්න.
2. Code push කරන්න (ඉහත commands).
3. **Secrets** add කරන්න (Settings → Secrets and variables → Actions).
4. **Personal Access Token** හදා `bot/.env` එකට add කරන්න.

---

## Section 4: Oracle Cloud Free VPS / Oracle Cloud නිදහස් VPS

**English:**

### 4.1 Sign Up
1. Go to https://www.oracle.com/cloud/free/
2. Sign up for Always Free account (requires a credit card for verification – you will NOT be charged for Always Free resources).
3. Choose a **home region** closest to you (e.g., Singapore `ap-singapore-1` for Sri Lanka).

### 4.2 Create VM Instance
1. In the Oracle Cloud console, go to **Compute → Instances → Create Instance**.
2. **Name**: `filmsite-bot`
3. **Image**: Ubuntu 22.04 LTS (Minimal)
4. **Shape**: `VM.Standard.A1.Flex` (ARM, Always Free) – allocate **2 OCPUs, 12 GB RAM**.
5. **SSH Keys**: Generate a key pair and download the private key (`.pem` file). Keep it safe!
6. Click **Create**.

### 4.3 Connect via SSH
```bash
# On Windows, use PowerShell or Git Bash:
chmod 400 ~/Downloads/ssh-key-xxxxx.key    # or icacls on Windows
ssh -i ~/Downloads/ssh-key-xxxxx.key ubuntu@<YOUR_VPS_PUBLIC_IP>
```

### 4.4 Install Dependencies on VPS
```bash
# Update system
sudo apt update && sudo apt upgrade -y

# Install Python 3.11, pip, git
sudo apt install -y python3 python3-pip git screen

# Install PM2 (process manager, keeps bot running)
curl -fsSL https://deb.nodesource.com/setup_20.x | sudo -E bash -
sudo apt install -y nodejs
sudo npm install -g pm2

# Clone your repo
git clone https://github.com/YOUR_USERNAME/film-web-site.git
cd film-web-site/bot

# Install Python dependencies
pip3 install -r requirements.txt

# Copy your session file to bot/ folder
# (transfer from your local machine using SCP or SFTP)
scp -i ~/Downloads/ssh-key.key myaccount.session ubuntu@<VPS_IP>:~/film-web-site/bot/
```

### 4.5 Run the Bot
```bash
# Option A: using screen (simple)
screen -S filmbot
cd ~/film-web-site/bot
python3 bot.py
# Press Ctrl+A, D to detach. Run `screen -r filmbot` to reattach.

# Option B: using PM2 (recommended – auto-restart on crash)
pm2 start "python3 bot.py" --name filmbot --cwd ~/film-web-site/bot
pm2 save
pm2 startup   # follow the printed instruction to enable auto-start on reboot
```

**සිංහල:**
1. https://www.oracle.com/cloud/free/ හි sign up කරන්න (credit card ඕනෑ, charge නොවෙයි).
2. **Compute → Instances → Create** → Ubuntu 22.04, ARM shape (VM.Standard.A1.Flex) select කරන්න.
3. SSH key generate කරන්නේ ඉහත steps follow කරමින්.
4. VPS connect වී ඉහත commands run කරන bot install කරන්න.

---

## Section 5: Domain + Cloudflare / Domain සහ Cloudflare

**English:**

### 5.1 Buy a Domain
- **For .lk domains**: https://www.nic.lk (register directly) or via https://www.ideamart.lk
- **For .com domains**: https://www.namecheap.com (cheapest) or https://porkbun.com

### 5.2 Add to Cloudflare
1. Create a free account at https://cloudflare.com
2. Click **Add a Site** → enter your domain → choose **Free** plan.
3. Cloudflare will scan your DNS records.
4. Change your domain's nameservers to Cloudflare's (shown in the setup wizard).
   - This is done at your domain registrar's control panel.

### 5.3 Connect to Cloudflare Pages
1. In Cloudflare dashboard: **Pages → Create a project → Connect to Git**.
2. Select your GitHub repository.
3. **Build settings**:
   - Framework: `None`
   - Build command: `node scripts/generate_sitemap.js`
   - Build output directory: `website`
4. **Environment variables**: Add `SITE_BASE_URL = https://yourdomain.com`
5. Deploy!

### 5.4 Add CNAME for Custom Domain
1. In Cloudflare Pages → your project → **Custom domains** → Add domain.
2. Enter `yoursite.lk` → Cloudflare automatically adds the CNAME.
3. Also add `www.yoursite.lk` and redirect it to `yoursite.lk`.

### 5.5 Get Cloudflare API Token (for GitHub Actions)
1. Cloudflare dashboard → **My Profile → API Tokens → Create Token**.
2. Use template: **Edit Cloudflare Workers** (or create custom with Pages:Edit permission).
3. Copy the token → add as `CF_API_TOKEN` in GitHub Secrets.
4. Your **Account ID** is shown on the right sidebar of the Cloudflare dashboard.

**සිංහල:**
1. .lk domain: https://www.nic.lk | .com domain: https://www.namecheap.com
2. https://cloudflare.com හි free account හදා domain add කරන්න.
3. Domain registrar panel එකේ nameservers Cloudflare nameservers ට change කරන්න.
4. Cloudflare Pages → GitHub repo connect කරන්න.
5. API Token හදා GitHub Secrets add කරන්න.

---

## Section 6: First Movie Upload Test / පළමු Movie Upload Test

**English:**
1. Start the bot (`pm2 start filmbot` or `screen -r filmbot`).
2. In Telegram, send `/start` to your bot.
3. Send `/add` to begin the movie addition workflow.
4. Follow the bot's prompts:
   - Send the movie video file to your Telegram channel.
   - Forward the channel message to the bot.
   - The bot fetches metadata from TMDB, updates `movies.json`, and pushes to GitHub.
5. GitHub Actions triggers automatically → generates sitemap → deploys to Cloudflare Pages.
6. Visit `https://yoursite.lk` – the new movie should appear within ~2 minutes!

**Verification checklist:**
- [ ] Bot responds to `/start`
- [ ] Movie appears on website homepage
- [ ] `https://yoursite.lk/sitemap.xml` shows the new movie URL
- [ ] `https://yoursite.lk/movie.html?id=movie-slug` plays the video
- [ ] GitHub Actions shows green checkmark ✅

**සිංහල:**
1. Bot start කරන්න.
2. Telegram හි bot ට `/start` send කරන්න.
3. `/add` send කර bot prompts follow කරන්න.
4. Movie video file channel ට upload කර bot ට forward කරන්න.
5. Bot TMDB metadata fetch කර `movies.json` update කර GitHub push කරයි.
6. ~2 minutes ඇතුළත website update වෙයි.

---

## Section 7: Monetization Setup / Monetization සැකසීම

**English:**

### 7.1 Monetag (Recommended for Sri Lanka)
1. Go to https://monetag.com and sign up as a Publisher.
2. Add your website URL and get approved (usually within 24-48 hours).
3. After approval, go to **Websites → Ad Zones → Create Zone**.
4. Choose ad type (Popunder, In-Page Push, or Banner).
5. Copy the ad code snippet.
6. Paste it in your HTML files, just before `</body>`:
   ```html
   <!-- Monetag Ad Code -->
   <script src="//...monetag.com/..."></script>
   ```
7. Update `website/ads.txt` with your Publisher ID (from Monetag dashboard → Account).
8. Expected earnings for Sri Lankan traffic: **\$1–5 CPM** for popunders.
   - 10,000 visitors/day × \$2 CPM = ~\$20/day = ~\$600/month

### 7.2 Adsterra (Alternative)
1. Go to https://adsterra.com → Publisher signup.
2. Add site → get ad codes → paste in HTML.
3. Update `website/ads.txt` with your Adsterra Publisher ID.

### 7.3 Where to Place Ad Code in Your Website
| File          | Location          | Ad type                  |
|---------------|-------------------|--------------------------|
| `index.html`  | Before `</body>`  | Popunder (main revenue)  |
| `movie.html`  | Above video player| Banner or In-Page Push   |
| `search.html` | Between results   | Native ads               |

### 7.4 Important Rules
- **Never click your own ads** (account ban).
- Keep the `ads.txt` file updated with real Publisher IDs.
- Don't place too many ads – it harms user experience and SEO.

**සිංහල:**
1. https://monetag.com හි Publisher signup කරන්න.
2. Website add කර approve වෙන්නට 24-48 hours wait කරන්න.
3. Ad code copy කර HTML files වල `</body>` ඉදිරියේ paste කරන්න.
4. `website/ads.txt` file publisher ID update කරන්න.
5. Sri Lanka traffic සඳහා popunder ads best: ~\$1-5 CPM.
6. **ඔබේ ads click කරන්නේ නැත** (ban වෙයි).

---

## Quick Reference – Environment Variables / env Variables සාරාංශය

Create `bot/.env` with these variables:

```env
# ── Telegram ──────────────────────────────────────────────────
API_ID=12345678
API_HASH=your_api_hash_here
BOT_TOKEN=7123456789:AAFxxxxxxxxxxxxxxxxxxxxxxxxxx
SESSION_NAME=myaccount
CHANNEL_ID=-1001234567890

# ── TMDB ──────────────────────────────────────────────────────
TMDB_API_KEY=your_tmdb_api_key_here

# ── GitHub ────────────────────────────────────────────────────
GITHUB_TOKEN=ghp_xxxxxxxxxxxxxxxxxxxxxxxxxxxx
GITHUB_REPO=YOUR_USERNAME/film-web-site
GITHUB_BRANCH=main

# ── Site ──────────────────────────────────────────────────────
SITE_BASE_URL=https://yoursite.lk

# ── Streaming (Cloudflare Worker) ─────────────────────────────
STREAM_SECRET_TOKEN=your_secret_token_here
VPS_PUBLIC_IP=xxx.xxx.xxx.xxx
```

---

## Troubleshooting / ගැටළු නිරාකරණය

| Problem | Solution |
|---------|----------|
| Bot doesn't respond | Check `pm2 logs filmbot` for errors |
| GitHub Actions fails | Check Actions tab → click failed run → read logs |
| Video doesn't play | Check CORS headers in Cloudflare Worker; check `stream.yoursite.lk/health` |
| Site not updating | Check that bot pushed to GitHub; check Actions triggered |
| `session.session` corrupted | Delete it and regenerate (Section 1.4) |
| Cloudflare 522 error | VPS is down – check Oracle Cloud console |

---

*Last updated: 2026-09-20 | FilmSite Automation Infrastructure v1.0*
