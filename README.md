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

