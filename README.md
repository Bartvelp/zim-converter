# ZIM-converter

This Python project converts ZIM files (Kiwix format) to optimized SQLite databases for the WikiReader plugin in KOReader. 

**Now supports both Wikipedia AND non-Wikipedia content** (iFixit, Wiktionary, etc.) with enhanced error handling and image processing options.
## WikiReader

I created this plugin for KoReader during sometime off: https://github.com/koreader/koreader/pull/9534
It has not been merged yet and probably never will be, but I am using it myself and is fairly stable and works for me.

## Just give me a database

If you have no experience programming or simply want a database file that is preconverted, that [can be found here](https://mega.nz/file/9zZlQIKC#ZDPEAQvo_jktEdaDn20AplywxXScJW5yOGB8BMfd1qA).
This database contains the top 60k popular articles of english wikipedia as of may 2025, with the top 10k articles containing images too. Note that the max file size is 4GB for the FAT32 filesystems of common ereaders, so this is about as much info as you can pack in a single DB. If you have an external sd card with NTFS or EXT4 you could convert the full wikipedia with images (~100 GiB). In theory it should work, but I have not tested it myself.

[Old database](https://mega.nz/file/06AX2DrC#1WYLi9GsF2DV7VplMaMoK7bKGWna2ItIeiW92OekALg). This database contains 114303 popular articles of english wikipedia as of september 2022.

## Converting ZIM files yourself

**Supported Content Types:**
- ✅ **Wikipedia** (all languages and variants)
- ✅ **iFixit** (repair guides with images)  
- ✅ **Wiktionary** (dictionaries)
- ✅ **Any ZIM file** (universal namespace support)

Conversion is optimized and includes comprehensive error handling and debug logging.

### How to get ZIM file

You can download a dump of WikiPedias most popular articles from their servers, or use a mirror [like this one](http://ftp.acc.umu.se/mirror/wikimedia.org/other/kiwix/zim/wikipedia/). I recommend using a dump starting with `wikipedia_en_top_nopic`.

On the command line you could for example do this:

```bash
wget -O wikipedia.zim http://ftp.acc.umu.se/mirror/wikimedia.org/other/kiwix/zim/wikipedia/wikipedia_en_top_nopic_2022-09.zim
```

### Running the CLI

```bash
# First install the dependencies with pip:
pip install -r requirements.txt

# Basic conversion (text-only, smallest database):
python3 zim_converter.py --zim-file ./wikipedia.zim --output-db ./zim_articles.db

# Include images (larger database):
python3 zim_converter.py --zim-file ./ifixit.zim --output-db ./ifixit.db --include-images

# Compress images for e-readers (requires ImageMagick):
# Converts images to grayscale, resizes, and heavily compresses
python3 zim_converter.py --zim-file ./ifixit.zim --output-db ./ifixit.db --compress-images
```

**Image Processing Options:**
- `--include-images`: Include original images (significantly increases size)
- `--compress-images`: Convert to grayscale, resize to 800x600, compress to <50KB (requires ImageMagick)

**Install ImageMagick for image compression:**
```bash
# Ubuntu/Debian:
sudo apt install imagemagick

# macOS:
brew install imagemagick

# Windows: Download from https://imagemagick.org/
```

Then transfer the `.db` file to your e-reader and set it as the database in the WikiReader plugin.

### Docker

You can manually install the 2 dependencies and just run the python file with appropriate arguments. But if needed
you can also build and run the docker if preferred, example when the zim file is called `wikipedia.zim` in the current dir:

```bash
docker build --tag zim-converter .
docker run --rm -it -v $(pwd):/project zim-converter --zim-file /project/wikipedia.zim --output-db /project/zim_articles.db
```
