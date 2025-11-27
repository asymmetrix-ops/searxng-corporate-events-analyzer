# 🚀 Deployment Guide

## Step 1: Push to GitHub

### Option A: Create New Repository on GitHub.com
1. Go to https://github.com/new
2. Repository name: `searxng-openrouter-research` (or your choice)
3. Description: "AI-powered company research tool using OpenRouter and SerpAPI"
4. Choose: **Private** (recommended) or Public
5. **DO NOT** initialize with README, .gitignore, or license
6. Click "Create repository"

### Option B: Use GitHub CLI (if installed)
```bash
gh repo create searxng-openrouter-research --private --source=. --remote=origin --push
```

### Manual Push (if Option B doesn't work)
```bash
# Add all files
git add .

# Commit
git commit -m "Initial commit: AI company research tool"

# Add your GitHub remote (replace USERNAME with your GitHub username)
git remote add origin https://github.com/USERNAME/searxng-openrouter-research.git

# Push to GitHub
git branch -M main
git push -u origin main
```

---

## Step 2: Deploy to Streamlit Cloud

1. Go to https://share.streamlit.io/
2. Click "New app"
3. Connect your GitHub account
4. Select repository: `searxng-openrouter-research`
5. Branch: `main`
6. Main file path: `app.py`
7. Click "Advanced settings" → Add secrets:

```
SERPAPI_KEY=your_serpapi_key_here
OPENROUTER_API_KEY=your_openrouter_key_here
SUPABASE_URL=your_supabase_url_here
SUPABASE_KEY=your_supabase_key_here
```

8. Click "Deploy" 🎉

---

## Step 3: Deploy to Railway (Alternative)

1. Go to https://railway.app/
2. Click "New Project" → "Deploy from GitHub repo"
3. Select your repository
4. Add environment variables in Railway dashboard:
   - `SERPAPI_KEY`
   - `OPENROUTER_API_KEY`
   - `SUPABASE_URL`
   - `SUPABASE_KEY`
5. Railway auto-detects Python and deploys!

**Note**: Create a `Procfile` for Railway:
```
web: streamlit run app.py --server.port $PORT --server.address 0.0.0.0
```

---

## 🔐 Security Notes

- ✅ `.env` is already in `.gitignore` - your secrets won't be pushed
- ✅ Use platform secrets management (Streamlit Cloud / Railway) for production
- ✅ Never commit API keys to GitHub

---

## 📝 Next Steps After Deployment

1. Test the app with a sample company
2. Monitor API usage (SerpAPI + OpenRouter dashboards)
3. Set up custom domain (optional)
4. Configure auto-deploy on push (enabled by default)

