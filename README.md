# ZIM-converter

This small python project allows you to convert ZIM files, as found in the Kiwix WikiPedia library, to a SQLite database
that is read by the WikiReader plugin of KOReader I am building.

Default is a single threaded conversion, but you can specify `--num-cores 4` to use more cores and thus speed up conversion.

## Just give me a database

If you have no experience programming or simply want a database file that is preconverted, that [can be found here](https://mega.nz/file/06AX2DrC#1WYLi9GsF2DV7VplMaMoK7bKGWna2ItIeiW92OekALg).
This database contains 114303 popular articles of english wikipedia as of september 2022.

## Converting ZIM files yourself

Conversion is pretty fast, the above database is converted from ZIM to my SQLite based format in about 1 to 3 minutes depending on the number of cores on my laptop.

### How to get ZIM file

You can download a dump of WikiPedias most popular articles from their servers, or use a mirror [like this one](http://ftp.acc.umu.se/mirror/wikimedia.org/other/kiwix/zim/wikipedia/). I recommend using a dump starting with `wikipedia_en_top_nopic`.

On the command line you could for example do this:

```bash
wget -O wikipedia.zim http://ftp.acc.umu.se/mirror/wikimedia.org/other/kiwix/zim/wikipedia/wikipedia_en_top_nopic_2022-09.zim
```

### Running the CLI

```bash
# First install the 2 dependencies with pip:
pip install -r requirements.txt
# Then run the command line interface like this:
python3 --zim-file ./wikipedia.zim --output-db ./zim_articles.db
```

Then simply transfer this `.db` file to a storage medium KOReader can access, and set it as the database in the plugin menu.

### Docker

You can manually install the 2 dependencies and just run the python file with appropriate arguments. But if needed
you can also build and run the docker if preferred, example when the zim file is called `wikipedia.zim` in the current dir:

```bash
docker build --tag zim-converter .
docker run --rm -it -v $(pwd):/project zim-converter --zim-file /project/wikipedia.zim --output-db /project/zim_articles.db
```
