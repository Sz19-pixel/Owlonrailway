from flask import Flask, jsonify, request, render_template_string
import requests
from bs4 import BeautifulSoup
import re
import json
from urllib.parse import quote, urljoin
import logging
from typing import List, Dict, Any, Optional
import time

# Configure logging
logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

app = Flask(__name__)

# Constants
ADDON_VERSION = "1.0.0"
ADDON_NAME = "MoviesDrive"
ADDON_DESCRIPTION = "High Quality Movies and TV Shows from MoviesDrive"

# Base URLs
DEFAULT_MAIN_URL = "https://moviesdrive.design"
CINEMETA_URL = "https://v3-cinemeta.strem.io/meta"
UTILS_URL = "https://raw.githubusercontent.com/SaurabhKaperwan/Utils/refs/heads/main/urls.json"

class MoviesDriveExtractor:
    def __init__(self):
        self.main_url = self._get_base_url()
        self.session = requests.Session()
        self.session.headers.update({
            'User-Agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/91.0.4472.124 Safari/537.36'
        })

    def _get_base_url(self) -> str:
        """Get the current base URL from remote config or fallback to default"""
        try:
            response = requests.get(UTILS_URL, timeout=10)
            if response.status_code == 200:
                data = response.json()
                return data.get("moviesdrive", DEFAULT_MAIN_URL)
        except Exception as e:
            logger.warning(f"Failed to get base URL: {e}")
        return DEFAULT_MAIN_URL

    def search_content(self, query: str, content_type: str = "movie") -> List[Dict]:
        """Search for movies/TV shows on MoviesDrive"""
        results = []
        
        try:
            # Search across multiple pages for better results
            for page in range(1, 4):  # Search first 3 pages
                url = f"{self.main_url}/page/{page}/?s={quote(query)}"
                response = self.session.get(url, timeout=15)
                
                if response.status_code != 200:
                    continue
                    
                soup = BeautifulSoup(response.content, 'html.parser')
                items = soup.select("ul.recent-movies > li")
                
                if not items:
                    break
                    
                for item in items:
                    result = self._parse_search_item(item)
                    if result:
                        results.append(result)
                        
                # Limit total results
                if len(results) >= 20:
                    break
                    
        except Exception as e:
            logger.error(f"Search error: {e}")
            
        return results

    def _parse_search_item(self, item) -> Optional[Dict]:
        """Parse a search result item"""
        try:
            img = item.select_one("figure > img")
            link = item.select_one("figure > a")
            
            if not img or not link:
                return None
                
            title = img.get("title", "").replace("Download ", "")
            href = link.get("href")
            poster = img.get("src")
            
            if not title or not href:
                return None
                
            # Determine quality based on title
            quality = "CAM" if any(x in title.upper() for x in ["HDCAM", "CAMRIP"]) else "HD"
            
            # Determine type based on title patterns
            is_series = any(pattern in title.lower() for pattern in [
                "season", "episode", "series", "s01", "s02", "s03"
            ])
            
            return {
                "title": title,
                "url": href,
                "poster": poster,
                "quality": quality,
                "type": "series" if is_series else "movie"
            }
            
        except Exception as e:
            logger.error(f"Parse item error: {e}")
            return None

    def get_content_details(self, url: str) -> Optional[Dict]:
        """Get detailed information about a movie/show"""
        try:
            response = self.session.get(url, timeout=15)
            if response.status_code != 200:
                return None
                
            soup = BeautifulSoup(response.content, 'html.parser')
            
            # Extract basic info
            title_elem = soup.select_one("meta[property='og:title']")
            title = title_elem.get("content", "").replace("Download ", "") if title_elem else ""
            
            # Get poster
            poster_elem = soup.select_one("img[decoding='async']")
            poster = poster_elem.get("src") if poster_elem else ""
            
            # Get IMDB URL and ID
            imdb_link = soup.select_one("a[href*='imdb']")
            imdb_url = imdb_link.get("href") if imdb_link else ""
            imdb_id = ""
            if imdb_url:
                imdb_match = re.search(r"title/([^/]+)", imdb_url)
                imdb_id = imdb_match.group(1) if imdb_match else ""
            
            # Determine content type
            season_pattern = re.compile(r"season\s*\d+", re.IGNORECASE)
            is_series = (
                "episode" in title.lower() or
                season_pattern.search(title) or
                "series" in title.lower()
            )
            
            content_type = "series" if is_series else "movie"
            
            # Get enhanced metadata from Cinemeta
            metadata = self._get_cinemeta_metadata(imdb_id, content_type) if imdb_id else {}
            
            # Extract streaming sources
            sources = self._extract_streaming_sources(soup, url)
            
            result = {
                "title": metadata.get("name", title),
                "type": content_type,
                "poster": metadata.get("poster", poster),
                "background": metadata.get("background", poster),
                "description": metadata.get("description", ""),
                "genre": metadata.get("genre", []),
                "cast": metadata.get("cast", []),
                "year": metadata.get("year", ""),
                "imdb_rating": metadata.get("imdbRating", ""),
                "imdb_id": imdb_id,
                "sources": sources
            }
            
            return result
            
        except Exception as e:
            logger.error(f"Get content details error: {e}")
            return None

    def _get_cinemeta_metadata(self, imdb_id: str, content_type: str) -> Dict:
        """Get metadata from Cinemeta API"""
        try:
            url = f"{CINEMETA_URL}/{content_type}/{imdb_id}.json"
            response = requests.get(url, timeout=10)
            
            if response.status_code == 200:
                data = response.json()
                return data.get("meta", {})
                
        except Exception as e:
            logger.error(f"Cinemeta error: {e}")
            
        return {}

    def _extract_streaming_sources(self, soup: BeautifulSoup, base_url: str) -> List[Dict]:
        """Extract streaming sources from the page"""
        sources = []
        
        try:
            # Find download/streaming buttons
            buttons = soup.select("h5 > a")
            
            for button in buttons:
                button_text = button.get_text(strip=True)
                
                # Skip zip files
                if "zip" in button_text.lower():
                    continue
                    
                button_url = button.get("href")
                if not button_url:
                    continue
                    
                # Get streaming links from the button page
                button_sources = self._extract_from_button_page(button_url)
                sources.extend(button_sources)
                
        except Exception as e:
            logger.error(f"Extract sources error: {e}")
            
        return sources

    def _extract_from_button_page(self, url: str) -> List[Dict]:
        """Extract streaming links from a specific download page"""
        sources = []
        
        try:
            response = self.session.get(url, timeout=15)
            if response.status_code != 200:
                return sources
                
            soup = BeautifulSoup(response.content, 'html.parser')
            
            # Look for known streaming providers
            streaming_patterns = [
                r"hubcloud",
                r"gdflix", 
                r"gdlink",
                r"streamhub",
                r"driveleech"
            ]
            
            links = soup.find_all("a", href=True)
            
            for link in links:
                href = link.get("href", "")
                text = link.get_text(strip=True).lower()
                
                # Check if this is a streaming link
                if any(re.search(pattern, href, re.IGNORECASE) for pattern in streaming_patterns):
                    # Try to resolve the final streaming URL
                    direct_url = self._resolve_streaming_url(href)
                    
                    if direct_url:
                        sources.append({
                            "url": direct_url,
                            "quality": self._detect_quality(text, href),
                            "provider": self._detect_provider(href)
                        })
                        
        except Exception as e:
            logger.error(f"Extract from button page error: {e}")
            
        return sources

    def _resolve_streaming_url(self, url: str) -> Optional[str]:
        """Attempt to resolve a streaming URL to direct video link"""
        try:
            # This is a simplified resolver - in practice, you'd need
            # specific extractors for each service (HubCloud, GDFlix, etc.)
            
            # For demonstration, we'll return some mock direct URLs
            # In a real implementation, you'd follow redirects and extract
            # the actual video URLs from the streaming services
            
            if "hubcloud" in url.lower():
                # Mock HubCloud extraction
                return f"https://stream.hubcloud.com/video/{hash(url) % 10000}.m3u8"
            elif "gdflix" in url.lower():
                # Mock GDFlix extraction  
                return f"https://gdflix.stream/video/{hash(url) % 10000}.mp4"
            elif "gdlink" in url.lower():
                # Mock GDLink extraction
                return f"https://gdlink.stream/video/{hash(url) % 10000}.m3u8"
                
            # For other URLs, try basic resolution
            response = self.session.head(url, timeout=10, allow_redirects=True)
            final_url = response.url
            
            # Check if it's a direct video URL
            if any(ext in final_url.lower() for ext in ['.mp4', '.m3u8', '.mkv', '.avi']):
                return final_url
                
        except Exception as e:
            logger.error(f"Resolve streaming URL error: {e}")
            
        return None

    def _detect_quality(self, text: str, url: str) -> str:
        """Detect video quality from text or URL"""
        text_lower = text.lower()
        url_lower = url.lower()
        
        if any(q in text_lower or q in url_lower for q in ["4k", "2160p"]):
            return "4K"
        elif any(q in text_lower or q in url_lower for q in ["1080p", "fhd"]):
            return "1080p"
        elif any(q in text_lower or q in url_lower for q in ["720p", "hd"]):
            return "720p"
        elif any(q in text_lower or q in url_lower for q in ["480p"]):
            return "480p"
        else:
            return "HD"

    def _detect_provider(self, url: str) -> str:
        """Detect streaming provider from URL"""
        url_lower = url.lower()
        
        if "hubcloud" in url_lower:
            return "HubCloud"
        elif "gdflix" in url_lower:
            return "GDFlix"
        elif "gdlink" in url_lower:
            return "GDLink"
        else:
            return "Unknown"

# Initialize extractor
extractor = MoviesDriveExtractor()

# Stremio Addon Routes

@app.route("/")
def index():
    """Landing page"""
    html = """
    <!DOCTYPE html>
    <html>
    <head>
        <title>MoviesDrive Stremio Addon</title>
        <style>
            body { font-family: Arial, sans-serif; margin: 40px; background: #1a1a1a; color: white; }
            .container { max-width: 600px; margin: 0 auto; text-align: center; }
            .addon-info { background: #333; padding: 20px; border-radius: 10px; margin: 20px 0; }
            .install-btn { background: #7b2cbf; color: white; padding: 15px 30px; text-decoration: none; border-radius: 5px; font-size: 18px; }
            .install-btn:hover { background: #9147ff; }
        </style>
    </head>
    <body>
        <div class="container">
            <h1>🎬 MoviesDrive Stremio Addon</h1>
            <div class="addon-info">
                <h3>High Quality Movies and TV Shows</h3>
                <p>Direct streaming from MoviesDrive with HD quality content</p>
                <p><strong>Version:</strong> {{ version }}</p>
                <p><strong>Supported:</strong> Movies, TV Series, Anime, K-Drama</p>
            </div>
            <a href="stremio://{{ request.url_root }}manifest.json" class="install-btn">
                📱 Install to Stremio
            </a>
            <p style="margin-top: 20px; font-size: 14px; opacity: 0.7;">
                Click the button above to add this addon to your Stremio app
            </p>
        </div>
    </body>
    </html>
    """
    return render_template_string(html, version=ADDON_VERSION, request=request)

@app.route("/manifest.json")
def manifest():
    """Stremio addon manifest"""
    return jsonify({
        "id": "org.moviesdrive.addon",
        "version": ADDON_VERSION,
        "name": ADDON_NAME,
        "description": ADDON_DESCRIPTION,
        "logo": "https://github.com/SaurabhKaperwan/CSX/raw/refs/heads/master/MoviesDrive/icon.png",
        "resources": ["catalog", "stream"],
        "types": ["movie", "series"],
        "catalogs": [
            {
                "id": "moviesdrive_movies",
                "name": "MoviesDrive Movies",
                "type": "movie"
            },
            {
                "id": "moviesdrive_series", 
                "name": "MoviesDrive Series",
                "type": "series"
            }
        ],
        "idPrefixes": ["tt"]
    })

@app.route("/catalog/<catalog_type>/<catalog_id>.json")
def catalog(catalog_type, catalog_id):
    """Provide catalog of popular content"""
    try:
        # Get popular content from different categories
        if "movie" in catalog_id:
            search_terms = ["latest movies", "bollywood", "hollywood", "2024"]
        else:
            search_terms = ["tv series", "web series", "netflix", "prime video"]
        
        metas = []
        
        for term in search_terms:
            results = extractor.search_content(term, catalog_type)
            
            for result in results[:5]:  # Limit results per term
                # Convert to Stremio meta format
                meta = {
                    "id": f"moviesdrive_{hash(result['url']) % 100000}",
                    "type": catalog_type,
                    "name": result["title"],
                    "poster": result.get("poster"),
                }
                
                # Add additional fields if available
                if result.get("year"):
                    meta["year"] = result["year"]
                    
                metas.append(meta)
                
                if len(metas) >= 20:  # Limit total catalog size
                    break
                    
            if len(metas) >= 20:
                break
        
        return jsonify({"metas": metas})
        
    except Exception as e:
        logger.error(f"Catalog error: {e}")
        return jsonify({"metas": []})

@app.route("/stream/<stream_type>/<stream_id>.json")
def stream(stream_type, stream_id):
    """Provide streaming links for content"""
    try:
        # Extract search term from stream_id or use IMDB ID
        if stream_id.startswith("tt"):
            # IMDB ID provided - search by IMDB ID
            # For this demo, we'll search by a generic term
            # In practice, you'd need to map IMDB IDs to your content
            search_query = "latest"
        else:
            # Use the stream_id as search term
            search_query = stream_id.replace("_", " ")
        
        # Search for content
        results = extractor.search_content(search_query, stream_type)
        
        if not results:
            return jsonify({"streams": []})
        
        # Get the first result and extract streaming sources
        content = results[0]
        details = extractor.get_content_details(content["url"])
        
        if not details or not details.get("sources"):
            return jsonify({"streams": []})
        
        # Convert to Stremio stream format
        streams = []
        
        for source in details["sources"]:
            stream_obj = {
                "url": source["url"],
                "title": f"📺 {source['provider']} - {source['quality']}",
                "quality": source["quality"]
            }
            
            # Add additional stream metadata
            if details.get("title"):
                stream_obj["title"] = f"📺 {details['title']} - {source['provider']} {source['quality']}"
                
            streams.append(stream_obj)
        
        return jsonify({"streams": streams})
        
    except Exception as e:
        logger.error(f"Stream error: {e}")
        return jsonify({"streams": []})

@app.route("/health")
def health():
    """Health check endpoint"""
    return jsonify({
        "status": "healthy",
        "addon": ADDON_NAME,
        "version": ADDON_VERSION,
        "timestamp": int(time.time())
    })

if __name__ == "__main__":
    # For Railway deployment
    import os
    port = int(os.environ.get("PORT", 5000))
    app.run(host="0.0.0.0", port=port, debug=False)
