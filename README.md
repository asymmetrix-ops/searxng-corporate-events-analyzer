🌐 Live Demo

🚀 Live App: Coming Soon
🛠 Docs: Coming Soon

(Add your Streamlit Cloud / Vercel link if you deploy)

⭐ Features
🔍 1. Smart Search Integration

Fetches Page 1 Google search results via SerpAPI

🧠 2. AI-Powered Insights

Company summary

Company profile

Subsidiaries

Top management

Corporate events (5-year history)

📄 3. Instant PDF Report

Generated with FPDF2

Clean formatting

One-click download

🗄 4. Supabase Storage

Stores reports

Stores search history

Retrieve past results automatically

🎛 5. Modern UI (Streamlit)

Responsive

User-friendly

Real-time progress indicators

📦 Tech Stack
Component	Technology
Frontend	Streamlit
Backend	Python 3.13
Database	Supabase
AI Engine	Gemini / GPT
Scraper	BeautifulSoup + Playwright
Search API	SerpAPI
PDF Generator	fpdf2
🧩 Project Structure
📦 searxng-ai
 ┣ 📜 app.py
 ┣ 📜 requirements.txt
 ┣ 📜 README.md
 ┣ 📜 searxng_analyzer.py
 ┣ 📜 searxng_db.py
 ┣ 📜 searxng_pdf.py
 ┣ 📁 screenshots/
 ┗ 📁 venv/

🚀 Installation Guide

Follow these steps carefully.

1️⃣ Clone Repository
git clone https://github.com/your-username/searxng-ai.git
cd searxng-ai

2️⃣ Create Virtual Environment
macOS / Linux:
python3 -m venv venv
source venv/bin/activate

Windows:
python -m venv venv
venv\Scripts\activate

3️⃣ Install Dependencies
pip install --upgrade pip
pip install -r requirements.txt


If errors appear:

pip cache purge
pip install -r requirements.txt

4️⃣ Install Playwright Browsers
playwright install

5️⃣ Setup Environment Variables

Create .env file:

SERPAPI_KEY=your_serpapi_key
SUPABASE_URL=your_supabase_url
SUPABASE_KEY=your_supabase_key
OPENAI_API_KEY=your_api_key
GEMINI_API_KEY=your_gemini_key


Load in Python:

from dotenv import load_dotenv
load_dotenv()

6️⃣ SerpAPI Setup (IMPORTANT!)

Install:

pip install serpapi


Use this import:

from serpapi.google_search import GoogleSearch

7️⃣ PDF Generation Setup

Install:

pip install fpdf2


Import:

from fpdf import FPDF

8️⃣ Run the App
streamlit run app.py


Your app will open at:

http://localhost:8501

🛠 Troubleshooting Guide
❌ ModuleNotFoundError: dotenv
pip install python-dotenv

❌ No module named serpapi
pip install serpapi

❌ No module named playwright
pip install playwright
playwright install

❌ No module named fpdf
pip install fpdf2

❌ Search not working

Check:

SERPAPI_KEY=xxxxxxxx

❌ Supabase errors

Verify:

SUPABASE_URL=https://xxxx.supabase.co
SUPABASE_KEY=service_role_key

🧪 Development Mode
streamlit run app.py --logger.level=debug
🌐 Live Demo

🚀 Live App: Coming Soon
🛠 Docs: Coming Soon

(Add your Streamlit Cloud / Vercel link if you deploy)

⭐ Features
🔍 1. Smart Search Integration

Fetches Page 1 Google search results via SerpAPI

🧠 2. AI-Powered Insights

Company summary

Company profile

Subsidiaries

Top management

Corporate events (5-year history)

📄 3. Instant PDF Report

Generated with FPDF2

Clean formatting

One-click download

🗄 4. Supabase Storage

Stores reports

Stores search history

Retrieve past results automatically

🎛 5. Modern UI (Streamlit)

Responsive

User-friendly

Real-time progress indicators

📦 Tech Stack
Component	Technology
Frontend	Streamlit
Backend	Python 3.13
Database	Supabase
AI Engine	Gemini / GPT
Scraper	BeautifulSoup + Playwright
Search API	SerpAPI
PDF Generator	fpdf2
🧩 Project Structure
📦 searxng-ai
 ┣ 📜 app.py
 ┣ 📜 requirements.txt
 ┣ 📜 README.md
 ┣ 📜 searxng_analyzer.py
 ┣ 📜 searxng_db.py
 ┣ 📜 searxng_pdf.py
 ┣ 📁 screenshots/
 ┗ 📁 venv/

🚀 Installation Guide

Follow these steps carefully.

1️⃣ Clone Repository
git clone https://github.com/your-username/searxng-ai.git
cd searxng-ai

2️⃣ Create Virtual Environment
macOS / Linux:
python3 -m venv venv
source venv/bin/activate

Windows:
python -m venv venv
venv\Scripts\activate

3️⃣ Install Dependencies
pip install --upgrade pip
pip install -r requirements.txt


If errors appear:

pip cache purge
pip install -r requirements.txt

4️⃣ Install Playwright Browsers
playwright install

5️⃣ Setup Environment Variables

Create .env file:

SERPAPI_KEY=your_serpapi_key
SUPABASE_URL=your_supabase_url
SUPABASE_KEY=your_supabase_key
OPENAI_API_KEY=your_api_key
GEMINI_API_KEY=your_gemini_key


Load in Python:

from dotenv import load_dotenv
load_dotenv()

6️⃣ SerpAPI Setup (IMPORTANT!)

Install:

pip install serpapi


Use this import:

from serpapi.google_search import GoogleSearch

7️⃣ PDF Generation Setup

Install:

pip install fpdf2


Import:

from fpdf import FPDF

8️⃣ Run the App
streamlit run app.py


Your app will open at:

http://localhost:8501

🛠 Troubleshooting Guide
❌ ModuleNotFoundError: dotenv
pip install python-dotenv

❌ No module named serpapi
pip install serpapi

❌ No module named playwright
pip install playwright
playwright install

❌ No module named fpdf
pip install fpdf2

❌ Search not working

Check:

SERPAPI_KEY=xxxxxxxx

❌ Supabase errors

Verify:

SUPABASE_URL=https://xxxx.supabase.co
SUPABASE_KEY=service_role_key

🧪 Development Mode
streamlit run app.py --logger.level=debug

---

## 🚀 Fly.io Deployment Guide

This project is deployed on Fly.io using FastAPI with a custom HTML frontend.

### Prerequisites

1. **Fly.io CLI**: Install the Fly.io CLI
   ```bash
   # macOS
   curl -L https://fly.io/install.sh | sh
   
   # Linux
   curl -L https://fly.io/install.sh | sh
   
   # Windows (using PowerShell)
   powershell -Command "iwr https://fly.io/install.ps1 -useb | iex"
   ```

2. **Fly.io Account**: Sign up at [fly.io](https://fly.io) and authenticate:
   ```bash
   fly auth login
   ```

### Environment Variables Setup

Before deploying, you need to set up your environment variables in Fly.io:

```bash
# Set environment variables
fly secrets set SERPAPI_KEY=your_serpapi_key
fly secrets set OPENROUTER_API_KEY=your_openrouter_key
fly secrets set XANO_BASE_URL=https://xdil-abvj-o7rq.e2.xano.io

# Optional: Set other API keys if needed
fly secrets set GEMINI_API_KEY=your_gemini_key
fly secrets set OPENAI_API_KEY=your_openai_key
```

**Important**: Never commit API keys to the repository. Always use `fly secrets set` for sensitive data.

### Deployment Steps

1. **Initialize Fly.io App** (if not already done):
   ```bash
   fly launch
   ```
   - This will create a `fly.toml` configuration file
   - Choose a unique app name or use the suggested one
   - Select a region (e.g., `iad` for US East)

2. **Review Configuration**:
   - Check `fly.toml` for app name and region settings
   - Verify `Dockerfile` is present and correct
   - Ensure `requirements.txt` includes all dependencies

3. **Deploy the Application**:
   ```bash
   fly deploy
   ```
   - This builds the Docker image and deploys to Fly.io
   - First deployment may take 5-10 minutes

4. **Check Deployment Status**:
   ```bash
   fly status
   fly logs
   ```

5. **Open Your App**:
   ```bash
   fly open
   ```
   Or visit: `https://your-app-name.fly.dev`

### Configuration Files

#### `fly.toml`
```toml
app = "searxng-corporate-events-analyzer"
primary_region = "iad"

[env]
  PORT = "8080"

[http_service]
  internal_port = 8080
  force_https = true
  auto_stop_machines = true
  auto_start_machines = true
  min_machines_running = 0
```

#### `Dockerfile`
- Uses Python 3.11 slim image
- Installs dependencies from `requirements.txt`
- Runs `uvicorn server:app` on port 8080

### Common Commands

```bash
# View logs
fly logs

# SSH into the running container
fly ssh console

# Scale the app
fly scale count 2

# View app status
fly status

# Open the app in browser
fly open

# Update secrets
fly secrets set KEY=value

# List secrets (values hidden)
fly secrets list

# Deploy new version
fly deploy

# View app info
fly info
```

### Troubleshooting

#### ❌ Build fails
- Check `requirements.txt` for all dependencies
- Verify `Dockerfile` syntax
- Review build logs: `fly logs`

#### ❌ App crashes on startup
- Check environment variables: `fly secrets list`
- Verify all required secrets are set
- Check application logs: `fly logs`

#### ❌ Port binding errors
- Ensure `PORT` environment variable is set to `8080`
- Verify `fly.toml` has correct `internal_port` setting
- Check `Dockerfile` CMD uses `${PORT:-8080}`

#### ❌ API key errors
- Verify all API keys are set: `fly secrets list`
- Check if keys are valid and have proper permissions
- Review server logs for specific error messages

### Updating the Application

1. **Make your changes** to the codebase

2. **Test locally**:
   ```bash
   uvicorn server:app --host 0.0.0.0 --port 8080
   ```

3. **Deploy updates**:
   ```bash
   fly deploy
   ```

4. **Monitor deployment**:
   ```bash
   fly logs
   ```

### Scaling

```bash
# Scale to 2 instances
fly scale count 2

# Scale memory
fly scale memory 1024

# Scale CPU
fly scale vm shared-cpu-2x
```

### Monitoring

- **View logs**: `fly logs`
- **App metrics**: Visit your Fly.io dashboard
- **Health checks**: Fly.io automatically monitors your app

### Cost Optimization

- **Auto-stop machines**: Enabled in `fly.toml` (`auto_stop_machines = true`)
- **Min machines**: Set to `0` to allow complete shutdown when idle
- **Memory**: Currently set to 512MB (adjust in `fly.toml` if needed)

### Production Checklist

- [ ] All environment variables set via `fly secrets set`
- [ ] `fly.toml` configured with correct app name and region
- [ ] `Dockerfile` builds successfully
- [ ] Application starts without errors
- [ ] HTTPS enabled (default in Fly.io)
- [ ] Logs are accessible and readable
- [ ] Health checks passing

### Support

- **Fly.io Docs**: https://fly.io/docs
- **Fly.io Community**: https://community.fly.io
- **Status Page**: https://status.fly.io

