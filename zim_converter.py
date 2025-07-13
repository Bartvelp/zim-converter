import os
import sqlite3
import zstd
import argparse
from libzim import Archive
from multiprocessing import Pool
import re
import base64
import time
import logging
import subprocess
import tempfile

# Setup logging (will be configured later based on --debug flag)
logger = logging.getLogger(__name__)

img_src_pattern = r'<img\s+[^>]*src=["\']([^"\']+)["\']'
css_link_pattern = r'<link\s+[^>]*href=["\']([^"\']+)["\'][^>]*rel=["\']stylesheet["\'][^>]*>'
MAX_IMAGE_SIZE = 300 * 1024  # 300 KB in bytes
MAX_COMPRESSED_IMAGE_SIZE = 15 * 1024  # 15 KB for e-ink optimized images

def compress_image_with_imagemagick(image_data, mime_type):
    """
    EXTREME e-ink optimization using ImageMagick:
    - Convert to 16-level grayscale with Floyd-Steinberg dithering
    - Resize to max 600x400 for smaller files
    - Enhance contrast for better e-ink visibility  
    - Very aggressive compression (quality 35)
    - Target: <15KB per image for optimal e-reader performance
    Returns compressed image data and new size, or None if compression fails
    """
    try:
        # Create temporary files
        with tempfile.NamedTemporaryFile(suffix=f'.{mime_type}', delete=False) as input_file:
            input_file.write(image_data)
            input_path = input_file.name
        
        with tempfile.NamedTemporaryFile(suffix='.jpg', delete=False) as output_file:
            output_path = output_file.name
        
        # ImageMagick command for EXTREME e-ink optimization:
        # - Convert to pure black/white with dithering for better e-ink rendering
        # - Aggressive resizing for small file sizes
        # - Minimal quality for maximum compression
        cmd = [
            'convert',
            input_path,
            '-colorspace', 'Gray',          # Convert to grayscale first
            '-resize', '600x400>',          # Smaller max size for e-ink
            '-contrast-stretch', '0.15x0.05%',  # Enhance contrast for e-ink
            '-dither', 'FloydSteinberg',    # Add dithering for better e-ink display
            '-colors', '16',                # Reduce to 16 gray levels (e-ink friendly)
            '-quality', '35',               # Very aggressive compression
            '-strip',                       # Remove all metadata
            '-interlace', 'none',           # Remove progressive encoding
            '-sampling-factor', '4:2:0',    # Aggressive chroma subsampling
            output_path
        ]
        
        result = subprocess.run(cmd, capture_output=True, text=True, timeout=30)
        
        if result.returncode == 0:
            # Read compressed image
            with open(output_path, 'rb') as f:
                compressed_data = f.read()
            
            # Clean up temp files
            os.unlink(input_path)
            os.unlink(output_path)
            
            if len(compressed_data) < MAX_COMPRESSED_IMAGE_SIZE:
                logger.debug(f"Image compressed: {len(image_data)} -> {len(compressed_data)} bytes ({len(compressed_data)/len(image_data)*100:.1f}%)")
                return compressed_data, 'jpeg'
            else:
                logger.debug(f"Compressed image still too large ({len(compressed_data)} bytes), skipping")
                return None, None
        else:
            logger.warning(f"ImageMagick conversion failed: {result.stderr}")
            # Clean up temp files
            try:
                os.unlink(input_path)
                os.unlink(output_path)
            except:
                pass
            return None, None
            
    except subprocess.TimeoutExpired:
        logger.warning("ImageMagick conversion timed out")
        return None, None
    except Exception as e:
        logger.error(f"Image compression failed: {e}")
        return None, None

def is_binary_content(path):
    """
    Check if a path points to binary/media content that shouldn't be processed as text
    """
    path_lower = path.lower()
    
    # Image file extensions
    image_extensions = ['.jpg', '.jpeg', '.png', '.gif', '.svg', '.webp', '.bmp', '.ico', '.tiff']
    # Media file extensions  
    media_extensions = ['.mp4', '.webm', '.ogg', '.mp3', '.wav', '.pdf']
    # Font and style extensions
    font_extensions = ['.woff', '.woff2', '.ttf', '.otf', '.eot']
    # Other binary extensions
    other_extensions = ['.zip', '.gz', '.tar', '.exe', '.bin']
    
    # Check file extension
    for ext in image_extensions + media_extensions + font_extensions + other_extensions:
        if path_lower.endswith(ext):
            return True
    
    # Check for known image hosting patterns
    if any(pattern in path_lower for pattern in [
        'images/', 'img/', 'static/', 'assets/', 'media/',
        '.amazonaws.com/', 'upload', 'thumbnail', 'logo'
    ]):
        return True
        
    return False

def setup_db(con):
    """Setup a SQLite database in the format expected by WikiReader
    """
    cursor = con.cursor()

    cursor.executescript("""
    PRAGMA journal_mode=WAL;

    CREATE TABLE IF NOT EXISTS articles (
        id INTEGER PRIMARY KEY,
        title TEXT NOT NULL UNIQUE,
        page_content_zstd BLOB NOT NULL
    );

    CREATE TABLE IF NOT EXISTS title_2_id  (
        id INTEGER NOT NULL,
        title_lower_case TEXT PRIMARY KEY
    );

    DROP TABLE IF EXISTS css;
    CREATE TABLE IF NOT EXISTS css  (
        content_zstd BLOB NOT NULL
    );

    -- Add indexes for better WikiReader compatibility
    CREATE INDEX IF NOT EXISTS idx_title_lower ON title_2_id(title_lower_case);
    CREATE INDEX IF NOT EXISTS idx_articles_title ON articles(title);

    """)
    con.commit()

def get_mime_type(path):
    ext = path.split('.')[-1].lower()
    if ext == 'jpg' or ext == 'jpeg':
        return 'jpeg'
    elif ext == 'png':
        return 'png'
    elif ext == 'svg':
        return 'svg'
    elif ext == 'gif':
        return 'gif'
    else:
        return 'jpeg'

def get_image_link(link: str, zim: Archive, compress_images=False):
    link = link.replace('../', '')
    try:
        entry = zim.get_entry_by_path(link)
        item = entry.get_item()
        
        original_size = len(item.content)
        
        # Check if we should compress the image
        if compress_images and hasattr(get_image_link, '_imagemagick_available'):
            if original_size > MAX_COMPRESSED_IMAGE_SIZE:  # Only compress if larger than target
                mime_type = get_mime_type(item.path)
                compressed_data, new_mime = compress_image_with_imagemagick(item.content.tobytes(), mime_type)
                
                if compressed_data:
                    # Use compressed image
                    base64_bytes = base64.b64encode(compressed_data)
                    base64_string = base64_bytes.decode('utf-8')
                    size = len(base64_bytes)
                    logger.debug(f"Using compressed image for {link}: {original_size} -> {len(compressed_data)} bytes")
                    return f"data:image/{new_mime};base64,{base64_string}", size
                else:
                    logger.debug(f"Compression failed for {link}, using original or skipping")
        
        # Use original image logic (with size check)
        if original_size > MAX_IMAGE_SIZE:
            logger.debug(f"Skipped image {link} ({original_size} bytes > max {MAX_IMAGE_SIZE})")
            return None, 0

        base64_bytes = base64.b64encode(item.content)
        base64_string = base64_bytes.decode('utf-8')
        mime_type = get_mime_type(item.path)
        size = len(base64_bytes)
        return f"data:image/{mime_type};base64,{base64_string}", size
    except KeyError:
        return None, 0

def get_css_content(link: str, zim: Archive):
    link = link.replace('../', '')
    try:
        entry = zim.get_entry_by_path(link)
        item = entry.get_item()
        content = item.content.tobytes().decode('utf-8')
        return content, len(content.encode('utf-8'))
    except Exception as e:
        # print(f"Failed to load CSS {link}: {e}")
        return None, 0

def replace_img_and_css_html(html: str, zim: Archive, compress_images=False):
    # Handle image sources
    img_sources = re.findall(img_src_pattern, html)
    for src in img_sources:
        image_link, size = get_image_link(src, zim, compress_images)
        if image_link:
            html = html.replace(src, image_link)
        else:
            pass
            # print('Failed for image', src)

    # Handle CSS links
    css_links = [] # re.findall(css_link_pattern, html)
    # print(css_links)
    css_links = [l for l in css_links if 'inserted_style' not in l]
    for href in css_links:
        css_content, size = get_css_content(href, zim)
        if css_content:
            style_tag = f"<style>/* EXTRACTED FROM {href} */{css_content}</style>"
            html = re.sub(
                rf'<link\s+[^>]*href=["\']{re.escape(href)}["\'][^>]*>',
                style_tag,
                html
            )
        else:
            # print('Failed for CSS', href)
            pass
    return html


def convert_zim(zim_path, db_path, article_list=None):
    """Process a range of a ZIM file into a seperate SQLite database"""
    logger.info(f"Starting ZIM conversion: {zim_path} -> {db_path}")
    
    con = sqlite3.connect(db_path)
    cursor = con.cursor()
    setup_db(con)

    try:
        zim = Archive(zim_path)
        logger.info(f"ZIM file loaded successfully. Entry count: {zim.entry_count}")
    except Exception as e:
        logger.error(f"Failed to load ZIM file {zim_path}: {e}")
        raise

    # Track processing statistics
    stats = {
        'total_entries': 0,
        'special_files_skipped': 0,
        'redirects_processed': 0,
        'articles_processed': 0,
        'binary_files_skipped': 0,
        'other_entries_skipped': 0,
        'processing_errors': 0
    }

    def all_entry_gen():
        logger.info("Using all_entry_gen - processing all entries")
        for id in range(0, zim.entry_count):
            try:
                # Try to use public API first, fall back to private if needed
                try:
                    zim_entry = zim.get_entry_by_id(id)
                except AttributeError:
                    logger.debug(f"Public get_entry_by_id not available, using private _get_entry_by_id for entry {id}")
                    zim_entry = zim._get_entry_by_id(id)
                yield zim_entry
            except Exception as e:
                logger.error(f"Failed to get entry {id}: {e}")
                stats['processing_errors'] += 1

    def selected_entry_gen():
        logger.info(f"Using selected_entry_gen - processing {len(article_list)} specific articles")
        for article_title in article_list:
            try:
                yield zim.get_entry_by_path('/A/' + article_title)
            except Exception as e:
                logger.error(f'Failed to get article {article_title}: {e}')
                stats['processing_errors'] += 1

    entry_gen = selected_entry_gen if article_list else all_entry_gen
    num_total = len(article_list) if article_list else zim.entry_count
    num_done = 0
    t0 = time.time()
    
    logger.info(f"Starting processing {num_total} entries")
    
    for zim_entry in entry_gen():
        stats['total_entries'] += 1
        
        # Log first 10 entries to understand structure
        if num_done < 10:
            logger.info(f"Entry {num_done}: path='{zim_entry.path}', title='{zim_entry.title}', is_redirect={zim_entry.is_redirect}")
        
        # Detect special files
        if zim_entry.path.startswith('-'):
            logger.debug(f"Skipping special file: {zim_entry.path}")
            stats['special_files_skipped'] += 1
            continue
        
        # deal with normal files
        if zim_entry.is_redirect:
            logger.debug(f"Processing redirect: {zim_entry.title} -> {zim_entry.path}")
            try:
                destination_entry = zim_entry.get_redirect_entry()
                # Try to use public API for index, fall back to private
                try:
                    dest_index = destination_entry.index
                except AttributeError:
                    logger.debug("Using private _index attribute for redirect destination")
                    dest_index = destination_entry._index
                
                cursor.execute("INSERT OR REPLACE INTO title_2_id VALUES(?, ?)", [
                    dest_index, zim_entry.title.lower()
                ])
                stats['redirects_processed'] += 1
            except Exception as e:
                logger.error(f"Failed to process redirect {zim_entry.title}: {e}")
                stats['processing_errors'] += 1
                
        elif zim_entry.path.startswith('A/'):  # Wikipedia articles
            logger.debug(f"Processing Wikipedia article: {zim_entry.title}")
            try:
                process_article_entry(zim_entry, cursor, zim, stats)
            except Exception as e:
                logger.error(f"Failed to process Wikipedia article {zim_entry.title}: {e}")
                stats['processing_errors'] += 1
                
        elif not zim_entry.path.startswith('A/') and len(zim_entry.path) > 2 and '/' in zim_entry.path:
            # This might be a non-Wikipedia article (like iFixit)
            namespace = zim_entry.path.split('/')[0]
            
            # Skip known binary/media files
            if is_binary_content(zim_entry.path):
                logger.debug(f"Skipping binary/media file: {zim_entry.path}")
                stats['binary_files_skipped'] += 1
                continue
                
            if num_done < 50:  # Log first 50 non-A/ entries to understand structure
                logger.info(f"Non-Wikipedia entry found: namespace='{namespace}', path='{zim_entry.path}', title='{zim_entry.title}'")
            
            # Try to process as article regardless of namespace
            try:
                process_article_entry(zim_entry, cursor, zim, stats)
                logger.debug(f"Successfully processed non-Wikipedia article: {zim_entry.title}")
            except Exception as e:
                logger.debug(f"Failed to process non-Wikipedia article {zim_entry.title}: {e}")
                stats['processing_errors'] += 1
        else:
            stats['other_entries_skipped'] += 1
            if num_done < 20:  # Log first 20 skipped entries
                logger.debug(f"Skipping other entry: path='{zim_entry.path}', title='{zim_entry.title}'")
            
        num_done += 1
        # Commit to db on disk every once in a while
        if num_done % 500 == 0:
            elapsed = time.time() - t0
            logger.info(f'{elapsed:.1f}s Committing batch to db, at entry {num_done} of {num_total}')
            logger.info(f"Stats so far: {stats}")
            con.commit()

    elapsed = time.time() - t0
    logger.info(f"Processing completed in {elapsed:.1f}s")
    logger.info(f"Final statistics: {stats}")
    
    # Add a default "Ebook" entry for WikiReader plugin compatibility
    try:
        cursor.execute("SELECT COUNT(*) FROM articles WHERE title = 'Ebook'")
        if cursor.fetchone()[0] == 0:
            default_content = """
            <html>
            <head><title>Welcome to WikiReader</title></head>
            <body>
            <h1>Welcome to WikiReader</h1>
            <p>This database contains articles converted from a ZIM file.</p>
            <p>Use the search function to find articles.</p>
            <p>Database statistics:</p>
            <ul>
            <li>Total articles: {articles_processed}</li>
            <li>Total entries processed: {total_entries}</li>
            <li>Binary files skipped: {binary_files_skipped}</li>
            </ul>
            </body>
            </html>
            """.format(**stats)
            
            compressed_content = zstd.compress(default_content.encode(), 9, 4)
            
            # Use a high ID that won't conflict
            default_id = 999999
            cursor.execute("INSERT OR REPLACE INTO articles VALUES(?, ?, ?)", [
                default_id, "Ebook", compressed_content
            ])
            cursor.execute("INSERT OR REPLACE INTO title_2_id VALUES(?, ?)", [
                default_id, "ebook"
            ])
            logger.info("Added default 'Ebook' page for WikiReader compatibility")
    except Exception as e:
        logger.warning(f"Failed to add default Ebook page: {e}")

    con.commit()
    con.close()
    return stats

def process_article_entry(zim_entry, cursor, zim, stats):
    """Process a single article entry (works for any namespace)"""
    # First make it findable
    try:
        # Try to use public API for index, fall back to private
        try:
            entry_index = zim_entry.index
        except AttributeError:
            logger.debug(f"Using private _index attribute for entry {zim_entry.title}")
            entry_index = zim_entry._index
            
        cursor.execute("INSERT INTO title_2_id VALUES(?, ?)", [
            entry_index, zim_entry.title.lower()
        ])
    except sqlite3.IntegrityError as e:
        if not 'UNIQUE constraint' in str(e):
            logger.error(f"Unexpected integrity error for {zim_entry.title}: {e}")
            raise e
        cursor.execute("SELECT id FROM title_2_id WHERE title_lower_case = ?", [zim_entry.title.lower()])
        result = cursor.fetchone()
        if result:
            cur_id = result[0]
            if cur_id != entry_index:
                cursor.execute("UPDATE title_2_id SET id = ? WHERE id = ?", [
                    entry_index, cur_id
                ])
                logger.debug(f"Updated duplicate title mapping for {zim_entry.title}")

    try:
        # Validate entry has meaningful content
        if not zim_entry.title or len(zim_entry.title.strip()) < 2:
            logger.debug(f"Skipping entry with invalid title: '{zim_entry.title}'")
            return
            
        page_content = bytes(zim_entry.get_item().content).decode()
        
        # Validate content is not empty/too short
        if len(page_content.strip()) < 100:
            logger.debug(f"Skipping entry with minimal content: {zim_entry.title} ({len(page_content)} chars)")
            return
            
        logger.debug(f"Extracted content for {zim_entry.title}: {len(page_content)} characters")
        
        # Process images/CSS only if requested (WARNING: can make DB much larger)
        if hasattr(process_article_entry, '_include_images') and process_article_entry._include_images:
            compress_images = hasattr(process_article_entry, '_compress_images') and process_article_entry._compress_images
            new_page_content = replace_img_and_css_html(page_content, zim, compress_images)
            logger.debug(f"Processed images/CSS for {zim_entry.title} (compression: {compress_images})")
        else:
            new_page_content = page_content
        
        zstd_page_content = zstd.compress(new_page_content.encode(), 9, 4)
        logger.debug(f"Compressed content for {zim_entry.title}: {len(zstd_page_content)} bytes")
        
        cursor.execute("INSERT OR REPLACE INTO articles VALUES(?, ?, ?)", [
            entry_index, zim_entry.title.replace("_", " "), zstd_page_content
        ])
        stats['articles_processed'] += 1
        
    except Exception as e:
        logger.error(f"Failed to process content for {zim_entry.title}: {e}")
        raise



if __name__ == "__main__":
    parser = argparse.ArgumentParser(description='Extract articles from ZIM files to SQLite database.')
    parser.add_argument(
        '--zim-file', help='Path of ZIM file',
        default="./wikipedia.zim"
    )
    parser.add_argument(
        '--output-db', help='Path where the SQLite database will be stored',
        default="./zim_articles.db"
    )
    parser.add_argument(
        '--article-list', help='Path of newline list of articles to extract from ZIM if you dont want to convert all entries',
        default=None, required=False
    )
    parser.add_argument(
        '--include-images', action='store_true',
        help='Include images in the conversion (WARNING: significantly increases database size)'
    )
    parser.add_argument(
        '--compress-images', action='store_true',
        help='EXTREME e-ink optimization: 16-level grayscale, dithered, <15KB per image (requires ImageMagick). Implies --include-images.'
    )
    parser.add_argument(
        '--debug', action='store_true',
        help='Enable debug logging to file and console'
    )
    args = parser.parse_args()
    
    # Configure logging based on debug flag
    if args.debug:
        logging.basicConfig(
            level=logging.DEBUG,
            format='%(asctime)s - %(levelname)s - %(message)s',
            handlers=[
                logging.FileHandler('zim_converter_debug.log'),
                logging.StreamHandler()
            ]
        )
        logger.info("Debug logging enabled - detailed output will be saved to zim_converter_debug.log")
    else:
        logging.basicConfig(
            level=logging.INFO,
            format='%(levelname)s: %(message)s',
            handlers=[logging.StreamHandler()]
        )

    logger.info(f"Starting ZIM conversion")
    logger.info(f"ZIM file: {args.zim_file}")
    logger.info(f"Output DB: {args.output_db}")
    
    # Check if ZIM file exists
    if not os.path.exists(args.zim_file):
        logger.error(f"ZIM file not found: {args.zim_file}")
        exit(1)

    # NOTE: Removed duplicate database connection setup here
    # convert_zim() handles its own connection to avoid conflicts
    
    if args.article_list:
        logger.info(f"Loading article list from: {args.article_list}")
        articles = open(args.article_list).read().splitlines()
        logger.info(f"Loaded {len(articles)} articles from list")
    else:
        articles = None
        logger.info("Processing all entries in ZIM file")
    
    # Check for ImageMagick if compression is requested
    if args.compress_images:
        try:
            result = subprocess.run(['convert', '-version'], capture_output=True, text=True, timeout=5)
            if result.returncode == 0:
                get_image_link._imagemagick_available = True
                logger.info("ImageMagick detected - image compression enabled")
                args.include_images = True  # Compress images implies include images
            else:
                logger.error("ImageMagick not found! Please install ImageMagick to use --compress-images")
                exit(1)
        except Exception as e:
            logger.error(f"Failed to check ImageMagick: {e}")
            logger.error("Please install ImageMagick to use --compress-images")
            exit(1)
    
    # Set image processing flags
    process_article_entry._include_images = args.include_images
    process_article_entry._compress_images = args.compress_images
    
    if args.compress_images:
        logger.warning("EXTREME e-ink image compression enabled!")
        logger.info(f"Images: 16-level grayscale, dithered, max 600x400, <{MAX_COMPRESSED_IMAGE_SIZE} bytes each")
    elif args.include_images:
        logger.warning("Image processing enabled - database size will be significantly larger!")
        logger.info(f"Max image size: {MAX_IMAGE_SIZE} bytes")
    else:
        logger.info("Image processing disabled - text-only conversion for smaller database")
    
    try:
        stats = convert_zim(args.zim_file, args.output_db, articles)
        logger.info(f"Conversion completed successfully!")
        logger.info(f"Final statistics: {stats}")
    except Exception as e:
        logger.error(f"Conversion failed: {e}")
        raise

    print('Done')
