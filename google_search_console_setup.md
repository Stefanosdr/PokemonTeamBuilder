# Google Search Console Setup Guide

This guide will help you get your Pokemon Team Generator app indexed on Google.

## Prerequisites

- Your app must be deployed and publicly accessible (e.g., on Streamlit Community Cloud)
- You need a Google account

## Step 1: Deploy Your App

If you haven't already, deploy your app to Streamlit Community Cloud:

1. Push your code to GitHub
2. Go to [share.streamlit.io](https://share.streamlit.io)
3. Sign in with GitHub
4. Click "New app"
5. Select your repository and branch
6. Set main file path to `ui_app.py`
7. Click "Deploy"
8. Note your app URL (e.g., `https://pokemonteamgenerator.streamlit.app`)

## Step 2: Set Up Google Search Console

### 2.1 Add Your Property

1. Go to [Google Search Console](https://search.google.com/search-console)
2. Click "Add Property"
3. Choose "URL prefix" (not Domain)
4. Enter your full app URL (e.g., `https://pokemonteamgenerator.streamlit.app`)
5. Click "Continue"

### 2.2 Verify Ownership

Google will ask you to verify that you own the site. For Streamlit apps, use the **HTML tag method**:

1. Select "HTML tag" verification method
2. Copy the meta tag provided (looks like: `<meta name="google-site-verification" content="YOUR_CODE_HERE" />`)
3. Add this tag to your `ui_app.py`:

```python
# In the main() function, add this with your other meta tags:
st.markdown("""
    <meta name="google-site-verification" content="YOUR_CODE_HERE">
""", unsafe_allow_html=True)
```

4. Commit and push the change to GitHub
5. Wait for Streamlit to redeploy (usually 1-2 minutes)
6. Go back to Google Search Console and click "Verify"

### 2.3 Submit Your URL for Indexing

1. Once verified, you'll be in the Search Console dashboard
2. In the left sidebar, click "URL Inspection"
3. Enter your app's URL
4. Click "Request Indexing"
5. Google will crawl your site within a few days

## Step 3: Monitor Performance

### Check Indexing Status

1. Go to "Coverage" in the left sidebar
2. This shows which pages are indexed
3. It may take 1-7 days for your site to appear in Google search results

### View Search Analytics

1. Go to "Performance" in the left sidebar
2. After a few weeks, you'll see:
   - How many times your site appeared in search results
   - Click-through rates
   - Which search queries led people to your site

## Step 4: Optimize for Better Rankings

### Update Your App URL in Meta Tags

Once deployed, update the Open Graph URL in `ui_app.py`:

```python
# Add this to your meta tags:
<meta property="og:url" content="https://pokemonteamgenerator.streamlit.app">
<link rel="canonical" href="https://pokemonteamgenerator.streamlit.app">
```

### Update robots.txt

Update the sitemap URL in `robots.txt`:

```
Sitemap: https://pokemonteamgenerator.streamlit.app/sitemap.xml
```

## Step 5: Promote Your App

To improve rankings faster:

1. **Share on Reddit**: Post to r/pokemon, r/stunfisk, r/pokemonshowdown
2. **Share on Smogon Forums**: Create a thread in the competitive discussion section
3. **Add to GitHub**: Make sure your GitHub repo has a good README with the app link
4. **Social Media**: Share on Twitter, Discord servers for competitive Pokémon

## Troubleshooting

### "URL is not on Google"
- This is normal for new sites. Wait 3-7 days after requesting indexing.

### "Crawl Error"
- Check that your app is publicly accessible
- Verify `robots.txt` allows crawling

### "Submitted URL not selected as canonical"
- Make sure you have the canonical link tag in your meta tags

## Expected Timeline

- **Day 1**: Submit to Search Console
- **Day 1-3**: Google crawls your site
- **Day 3-7**: Site appears in search results
- **Week 2-4**: Search rankings improve as Google understands your content
- **Month 2+**: Steady traffic from search results (if you promote the app)

## Tips for Better SEO

1. **Get backlinks**: Every link from another site helps your ranking
2. **Update regularly**: Add new features and update your app description
3. **Use keywords naturally**: Include "pokemon team builder", "competitive pokemon", etc. in your content
4. **Monitor Search Console**: Check weekly for issues or opportunities

---

**Questions?** Check the [Google Search Console Help Center](https://support.google.com/webmasters)
