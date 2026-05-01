cd /Users/ivcloud/Desktop/SearXNG-OpenRouter-30-10-main
source venv/bin/activate
python3 -c 'from dotenv import load_dotenv; load_dotenv(); import os; print("OPENROUTER_API_KEY:", bool(os.getenv("OPENROUTER_API_KEY") or os.getenv("OPEN_ROUTER_KEY"))); print("SERPAPI_KEY:", bool(os.getenv("SERPAPI_KEY")))'

## Run (FastAPI UI - default)
uvicorn server:app --reload --host 127.0.0.1 --port 8000
# open http://127.0.0.1:8000

## Run (Streamlit - optional legacy, not the main UI)
# streamlit run app.py --server.port 8502
# open http://localhost:8502