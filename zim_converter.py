import os
import sqlite3
import zstd
import argparse
from libzim import Archive
from multiprocessing import Pool
import re
import base64
import time

img_src_pattern = r'<img\s+[^>]*src=["\']([^"\']+)["\']'
css_link_pattern = r'<link\s+[^>]*href=["\']([^"\']+)["\'][^>]*rel=["\']stylesheet["\'][^>]*>'
MAX_IMAGE_SIZE = 300 * 1024  # 300 KB in bytes

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

def get_image_link(link: str, zim: Archive):
    link = link.replace('../', '')
    try:
        entry = zim.get_entry_by_path(link)
        item = entry.get_item()
        
        if len(item.content) > MAX_IMAGE_SIZE:
            print(f"Skipped image {link} ({len(item.content)} bytes > max {MAX_IMAGE_SIZE})")
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

def replace_img_and_css_html(html: str, zim: Archive):
    # Handle image sources
    img_sources = re.findall(img_src_pattern, html)
    for src in img_sources:
        image_link, size = get_image_link(src, zim)
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

    con = sqlite3.connect(db_path)
    cursor = con.cursor()
    setup_db(con)

    zim = Archive(zim_path)
    def all_entry_gen():
        for id in range(0, zim.entry_count):
            zim_entry = zim._get_entry_by_id(id)
            yield zim_entry

    def selected_entry_gen():
        for article_title in article_list:
            try:
                yield zim.get_entry_by_path('/A/' + article_title)
            except Exception as e:
                print('Failed to get', article_title, e)

    entry_gen = selected_entry_gen if article_list else all_entry_gen
    num_total = len(article_list) if article_list else zim.entry_count
    num_done = 0
    t0 = time.time()
    for zim_entry in entry_gen():
        # Detect special files
        if zim_entry.path.startswith('-'):
            # Don't continue parsing them as articles
            continue
        
        # deal with normal files
        if zim_entry.is_redirect:
            destination_entry = zim_entry.get_redirect_entry()
            cursor.execute("INSERT OR REPLACE INTO title_2_id VALUES(?, ?)", [
                destination_entry._index, zim_entry.title.lower()
            ])
        elif zim_entry.path.startswith('A/'):  # It is a proper article
            # First make it findable
            try:
                cursor.execute("INSERT INTO title_2_id VALUES(?, ?)", [
                    zim_entry._index, zim_entry.title.lower()
                ])
            except sqlite3.IntegrityError as e:
                if not 'UNIQUE constraint' in str(e):
                    raise e
                cursor.execute("SELECT id FROM title_2_id WHERE title_lower_case = ?", [ zim_entry.title.lower() ])
                cur_id = cursor.fetchone()[0]
                if cur_id != zim_entry._index:
                    cursor.execute("UPDATE title_2_id SET id = ? WHERE id = ?", [
                        zim_entry._index, cur_id
                    ])

            page_content = bytes(zim_entry.get_item().content).decode()
            # Try to find image links in the article, and replace them with their values if found
            new_page_content = replace_img_and_css_html(page_content, zim)

            zstd_page_content = zstd.compress(new_page_content.encode(), 9, 4)
            cursor.execute("INSERT OR REPLACE INTO articles VALUES(?, ?, ?)", [
                zim_entry._index, zim_entry.title.replace("_", " "), zstd_page_content
            ])
        num_done += 1
        # Commit to db on disk every once in a while
        if num_done % 500 == 0:
            print(f'{(time.time() - t0):.1f}s Commiting batch to db, at i {num_done} of {num_total}')
            con.commit()

    con.commit()
    con.close()
    return args



if __name__ == "__main__":
    parser = argparse.ArgumentParser(description='Extract wikipedia articles from zim DB.')
    parser.add_argument(
        '--zim-file', help='Path of zimfile',
        default="./wikipedia.zim"
    )
    parser.add_argument(
        '--output-db', help='Path where the html database will be stored',
        default="./zim_articles.db"
    )
    parser.add_argument(
        '--article-list', help='Path of newline list of articles to extract from ZIM if you dont want to convert all entries',
        default=None, required=False
    )
    args = parser.parse_args()

    # Setup db connection
    processed_ids = {}
    con = sqlite3.connect(args.output_db)
    cursor = con.cursor()
    setup_db(con)

    # Now perform the jobs single or multithreaded
    print(f'Starting conversion')

    if args.article_list:
        articles = open(args.article_list).read().splitlines()
    else:
        articles = None
    convert_zim(args.zim_file, args.output_db, articles)

    print('Done')
