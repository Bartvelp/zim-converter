import os
import sqlite3
import zstd
import argparse
from libzim import Archive
from multiprocessing import Pool


def merge_databases(db1: str, db2: str):
    """Merges all entries from db2 into db1
    """
    con3 = sqlite3.connect(db1)
    print("merging:", db2)
    con3.execute("ATTACH '" + db2 + "' as dba")

    con3.execute("BEGIN")
    for row in con3.execute("SELECT * FROM dba.sqlite_master WHERE type='table'"):
        combine = "INSERT OR IGNORE INTO " + row[1] + " SELECT * FROM dba." + row[1]
        con3.execute(combine)
    con3.commit()
    con3.execute("detach database dba")


def setup_db(con):
    """Setup a SQLite database in the format expected by WikiReader
    """
    cursor = con.cursor()

    cursor.executescript("""
    PRAGMA journal_mode=DELETE;

    CREATE TABLE IF NOT EXISTS articles (
        id INTEGER PRIMARY KEY,
        title TEXT NOT NULL,
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


def process_range(args):
    """Process a range of a ZIM file into a seperate SQLite database"""
    start_id, end_id, zim_path, db_path = args

    con = sqlite3.connect(db_path)
    cursor = con.cursor()
    setup_db(con)

    zim = Archive(zim_path)
    for id in range(start_id, end_id):
        zim_entry = zim._get_entry_by_id(id)

        # Detect special files
        if zim_entry.path.startswith('-'):
            if zim_entry.title.endswith(".css") and False:  # Disabled for now
                css_content = bytes(zim_entry.get_item().content)
                css = css_content
                cursor.execute("UPDATE css SET content_zstd = ?", [zstd.compress(css)])

            # Don't continue parsing them as articles
            continue

        # deal with normal files
        if zim_entry.is_redirect:
            destination_entry = zim_entry.get_redirect_entry()
            cursor.execute("INSERT OR REPLACE INTO title_2_id VALUES(?, ?)", [
                destination_entry._index, zim_entry.title.lower()
            ])
        else:  # It is a proper article
            # First make it findable
            cursor.execute("INSERT OR REPLACE INTO title_2_id VALUES(?, ?)", [
                zim_entry._index, zim_entry.title.lower()
            ])

            page_content = bytes(zim_entry.get_item().content)
            zstd_page_content = zstd.compress(page_content)
            cursor.execute("INSERT OR REPLACE INTO articles VALUES(?, ?, ?)", [
                zim_entry._index, zim_entry.title.replace("_", " "), zstd_page_content
            ])
        # Commit to db on disk every once in a while
        if id % 10000 == 0:
            print(f'Commiting batch to db, at i {id} of {end_id}')
            con.commit()

    con.commit()
    con.close()
    print('Done with batch, at id:', start_id, end_id)
    return args


def convert_multithreaded(args, num_cores=None):
    # Create jobs for the job pool
    zim = Archive(args.zim_file)
    batch_size = 5000
    end = zim.entry_count

    tasks = [(start_i,
             min(start_i + batch_size + 1, end),
             args.zim_file, args.output_db+f'_{start_i}'
              ) for start_i in range(0, end, batch_size)
             ]

    print("Created tasks")

    # Process jobs with pool
    with Pool(num_cores) as pool:
        results = pool.imap(process_range, tasks)
        for task in results:
            _, _, _, db_path = task
            merge_databases(args.output_db, db_path)
            os.remove(db_path)  # Delete temp db file after done merging


def convert_singlethreaded(args):
    zim = Archive(args.zim_file)
    task = (0, zim.entry_count, args.zim_file, args.output_db)
    process_range(task)


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
        '--num-cores', help='Number of cores used for conversion, default is single threaded',
        default=1,
        type=int
    )
    args = parser.parse_args()

    # Setup db connection
    processed_ids = {}
    con = sqlite3.connect(args.output_db)
    cursor = con.cursor()
    setup_db(con)

    # Now perform the jobs single or multithreaded
    print(f'Starting conversion with {args.num_cores} cores')
    if args.num_cores == 1:
        convert_singlethreaded(args)
    else:
        convert_multithreaded(args, args.num_cores)

    print('Done')
