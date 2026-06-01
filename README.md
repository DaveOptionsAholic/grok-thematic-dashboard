---
title: Grok Thematic Dashboard
emoji: 📈
colorFrom: '#636EFA'
colorTo: '#0E1117'
sdk: streamlit
sdk_version: '1.32.0'
app_file: streamlit_app.py
pinned: false
---

# Grok Thematic Dashboard v15

7 thematic portfolios with sidebar controls and configurable charts.

**Live version marker:** green `Dashboard v15.2` badge in the left sidebar.

## Run locally

```bash
pip install -r requirements.txt
streamlit run streamlit_app.py
```

## Streamlit Community Cloud (new app)

1. https://share.streamlit.io → **Create app**
2. Repo: `DaveOptionsAholic/grok-thematic-dashboard`
3. Branch: `deploy-v15` (recommended) or `main`
4. Main file: `streamlit_app.py`

## Hugging Face Spaces

Create a new **Streamlit** Space and push this repo, or duplicate files with `app_file: streamlit_app.py` in README frontmatter above.