#!/bin/bash

# Script to push code to GitHub
# Usage: ./push_to_github.sh YOUR_GITHUB_USERNAME REPO_NAME

if [ -z "$1" ] || [ -z "$2" ]; then
    echo "Usage: ./push_to_github.sh YOUR_GITHUB_USERNAME REPO_NAME"
    echo "Example: ./push_to_github.sh johndoe searxng-openrouter-research"
    exit 1
fi

USERNAME=$1
REPO_NAME=$2

echo "🚀 Pushing to GitHub..."
echo "Repository: https://github.com/$USERNAME/$REPO_NAME"
echo ""

# Check if remote already exists
if git remote get-url origin > /dev/null 2>&1; then
    echo "⚠️  Remote 'origin' already exists. Updating..."
    git remote set-url origin https://github.com/$USERNAME/$REPO_NAME.git
else
    echo "➕ Adding remote 'origin'..."
    git remote add origin https://github.com/$USERNAME/$REPO_NAME.git
fi

# Push to GitHub
echo "📤 Pushing to GitHub..."
git branch -M main
git push -u origin main

if [ $? -eq 0 ]; then
    echo ""
    echo "✅ Successfully pushed to GitHub!"
    echo "🌐 View your repo: https://github.com/$USERNAME/$REPO_NAME"
    echo ""
    echo "📋 Next steps:"
    echo "   1. Go to https://share.streamlit.io/"
    echo "   2. Connect your GitHub account"
    echo "   3. Select repository: $REPO_NAME"
    echo "   4. Add secrets (API keys) in Streamlit Cloud dashboard"
    echo "   5. Deploy! 🎉"
else
    echo ""
    echo "❌ Push failed. Make sure:"
    echo "   1. Repository exists on GitHub"
    echo "   2. You have write access"
    echo "   3. You're authenticated (use GitHub CLI or SSH keys)"
fi

