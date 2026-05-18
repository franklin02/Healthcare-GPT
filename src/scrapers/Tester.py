from .scraper_engine import clean_html_text

html = """
<html>
<body>
<nav>Navigation junk</nav>
<article>
    <h1>Real Article Title</h1>
    <p>This is the real healthcare disruption article.</p>
</article>
<div class="newsletter">Subscribe to our newsletter</div>
<div class="sidebar">Related articles</div>
</body>
</html>
"""

print(clean_html_text(html))