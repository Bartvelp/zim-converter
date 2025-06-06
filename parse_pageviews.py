import time
import sqlite3
import subprocess
import argparse


MIN_MONTLY_PAGE_COUNT = 500


def setup_db(con):
    cursor = con.cursor()

    cursor.executescript("""
    PRAGMA journal_mode=WAL;

    CREATE TABLE IF NOT EXISTS pageviews (
        wiki_domain_id TEXT PRIMARY KEY,
        wiki_id INTEGER NOT NULL,
        domain TEXT NOT NULL,
        view_count INTEGER NOT NULL
    );

    CREATE TABLE IF NOT EXISTS title_2_wiki_domain_id  (
        wiki_domain_id TEXT NOT NULL,
        title TEXT PRIMARY KEY
    );


    """)
    con.commit()


def getbz2_multithreaded(url: str, silent=False):
    curlOutput = subprocess.Popen(('curl', '--silent', '-L', url), stdout=subprocess.PIPE)

    if silent:
        rawOutput = subprocess.Popen(('lbzip2', '-d'), stdin=curlOutput.stdout, stdout=subprocess.PIPE)
    else:
        networkOutput = subprocess.Popen(('pv', '-cN', 'network'), stdin=curlOutput.stdout, stdout=subprocess.PIPE)
        lbzipOutput = subprocess.Popen(('lbzip2', '-d'), stdin=networkOutput.stdout, stdout=subprocess.PIPE)
        rawOutput = subprocess.Popen(('pv', '-cN', 'raw'), stdin=lbzipOutput.stdout, stdout=subprocess.PIPE)

    return rawOutput.stdout


def parse_pageviews_xml_url(url: str, con: sqlite3.Connection, pageview_stats: dict, valid_domains: list):
    cursor = con.cursor()
    num_bytes_read = 0
    num_lines_parsed = 0
    start_time = time.time()

    input_fh = getbz2_multithreaded(url)
    for line in input_fh:
        # Convert to string if needed
        if type(line) is bytes:
            line = line.decode("utf-8")[:-1]
        num_bytes_read += len(line)
        num_lines_parsed += 1

        # Report progress every once in a while
        if num_lines_parsed % 400000 == 0 and False:
            # Currently done by pipeviewer
            time_passed = time.time() - start_time
            print(f"Total speed: {(num_bytes_read / 1048576):.1f} MiB / {time_passed:.0f} seconds")

        # Break up the line
        line_parts = line.split(' ')
        if (len(line_parts) < 4):
            continue

        # Extract relevant page information
        domain = line_parts[0]
        page_name = line_parts[1]
        page_wiki_id = line_parts[2]
        # page_count_str = line_parts[-1]
        page_count = int(line_parts[-2])
        if page_wiki_id == "null":
            continue
        # if domain != "en.wikipedia":
        #     continue
        # if page_name.startswith("Category") or page_name.startswith("Talk"):
        #     continue

        if page_count < MIN_MONTLY_PAGE_COUNT:
            continue
        if domain not in valid_domains:
            continue

        # Add new page entries into the stats dict if they do not exist
        if domain not in pageview_stats:
            pageview_stats[domain] = {}
        if page_wiki_id not in pageview_stats[domain]:
            pageview_stats[domain][page_wiki_id] = 0

        # Increment the counter for this page with the page_count
        pageview_stats[domain][page_wiki_id] += page_count

        total_page_count = pageview_stats[domain][page_wiki_id]
        wiki_domain_id = f'{domain}-{page_wiki_id}'

        cursor.execute("INSERT OR REPLACE INTO pageviews VALUES(?, ?, ?, ?)", (
                wiki_domain_id, page_wiki_id, domain, total_page_count
            )
        )

        cursor.execute("INSERT OR REPLACE INTO title_2_wiki_domain_id VALUES(?, ?)", (
                wiki_domain_id, page_name
            )
        )
        if num_lines_parsed % 10000 == 0:
            con.commit()
    print('Done with bz2 file')
    con.commit()


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description='Process wikipedia pageviews dump.')
    parser.add_argument(
        '--pageview-url', help='URL of pageview dump .bz2',
        default="http://ftp.acc.umu.se/mirror/wikimedia.org/other/pageview_complete/monthly/2022/2022-08/pageviews-202208-user.bz2"
    )
    parser.add_argument(
        '--database-path', help='Path where the database will be stored',
        default="pageviews.db"
    )
    args = parser.parse_args()

    con = sqlite3.connect(args.database_path)
    setup_db(con)
    pageview_stats = {}

    valid_domains = ["en.wikipedia"]
    parse_pageviews_xml_url(args.pageview_url, con, pageview_stats, valid_domains)

    print("done")
