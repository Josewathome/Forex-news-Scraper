follow this gemin link on josephgathithi@gmail.com : https://gemini.google.com/app/ff2a71f3025a5ef8


for the scraping tool in claude: in the tana email:  https://claude.ai/chat/846067e2-4b0c-49ba-86b1-fbe589f3f86d



Here’s a clean and professional `README.md` you can use 👇

---

````markdown
# Forex News Scraper

This project uses **FastAPI + Playwright** to scrape forex news data.

---

## 🚀 Setup Guide

Follow the steps below to get the project running locally.

---

## 1. 📦 Create Virtual Environment

```bash
python3 -m venv venv
source venv/bin/activate
````

---

## 2. 📥 Install Python Dependencies

```bash
pip install -r requirements.txt
```

---

## 3. 🌐 Install Playwright Browsers

```bash
playwright install chromium
```

---

## 4. ⚙️ Install System Dependencies (Ubuntu / WSL)

If you're using **Ubuntu 24.04 / WSL**, install the required system libraries:

```bash
sudo apt-get update
sudo apt-get install -y \
    libnss3 \
    libnspr4 \
    libasound2t64 \
    libatk1.0-0t64 \
    libatk-bridge2.0-0t64 \
    libcups2t64 \
    libdrm2 \
    libxkbcommon0 \
    libxcomposite1 \
    libxdamage1 \
    libxrandr2 \
    libgbm1 \
    libxshmfence1 \
    libpango-1.0-0 \
    libcairo2 \
    libatspi2.0-0t64 \
    libgtk-3-0t64
```

---

## 5. 🔧 Install Additional Required Libraries

```bash
sudo apt-get install -y libicu-dev libffi-dev libx264-dev
```

---

## 6. ▶️ Run the Application

```bash
python main.py
```

---

## 🧠 Why These Steps Are Needed

Playwright runs a real browser (**Chromium**) under the hood.
Unlike typical Python libraries, this requires **system-level dependencies** (graphics, fonts, audio, etc.).

On **Ubuntu 24.04**, many packages were renamed (e.g., `libasound2 → libasound2t64`), which can cause installation failures if not handled correctly.

---

## ✅ Expected Result

After setup, the server should start successfully:

```
INFO:     Started server process
INFO:     Waiting for application startup.
```

---

## ⚠️ Troubleshooting

* If Playwright fails → ensure all system dependencies are installed
* If `playwright` command not found → activate your virtual environment
* If still failing → reinstall browsers:

```bash
playwright install --force
```

---

## 📌 Notes

* Tested on **Ubuntu 24.04 (WSL)**
* Python version: **3.12**

---

## 👨‍💻 Author

Joseph Wathome

```

---

If you want, I can also:
- Add Docker setup
- Add API usage examples
- Clean it for GitHub (badges, structure, etc.)
```
