# ZIM-converter

This small python project allows you to convert ZIM files, as found in the Kiwix WikiPedia library, to a SQLite database
that is read by the WikiReader plugin of KOReader I am building.

Default is a single threaded conversion, but you can specify `--num-cores 4` to use more cores and thus speed up conversion.

## Docker

You can manually install the 2 dependencies and just run the python file with appropriate arguments. But if needed
you can also build and run the docker if preferred, example when the zim file is called `wikipedia.zim` in the current dir:

```bash
docker build --tag zim-converter .
docker run --rm -it -v $(pwd):/project zim-converter --zim-file /project/wikipedia.zim --output-db /project/zim_articles.db
```
