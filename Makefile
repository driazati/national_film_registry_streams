all:
	cat films.tsv | awk -F '\t' '{print $$1 "\t" $$2}' | python generate.py > README.md
