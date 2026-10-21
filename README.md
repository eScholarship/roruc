# RORUC
## Setup
- [Install pyenv](https://github.com/pyenv/pyenv?tab=readme-ov-file#installation)
- [pyenv-virtualenv](https://github.com/pyenv/pyenv-virtualenv)
- Install python 3.14 and create virtualenv
```
pyenv install 3.14.8
pyenv virtualenv 3.14.8 roruc-venv
pyenv local roruc-venv
```
Create a `.env` file
dev:
```
DEBUG=True
HOST_URL=127.0.0.1
DB_HOST=postgres.orb.local # depends on your local docker host
DB_PASSWORD=<your-password>
```
prd:
```
SECRET_KEY='your-secret-key'
HOST_URL=<your-host-url>
DB_HOST=
DB_PASSWORD=
```
Run local postgres in docker for development
```
docker run --name postgres -p 5432:5432 -e POSTGRES_PASSWORD=<your-password> -d postgres
```
Install requirements and setup db
```
pip install -r requirements.txt
pre-commit install
python ./manage.py migrate
python ./manage.py runserver
```
## Tests
Run tests
```
python ./manage test
```
## Management commands
```
python manage.py import_units all_units.tsv all_relations.tsv (--dry-run)

python manage.py export_unit 7k3m9x2q (-o unit.json) (--id-prefix  https://our-ror.example.edu/)
python manage.py export_unit westjem (-o unit.json) (--id-prefix  https://our-ror.example.edu/)

python manage.py export_units (-o all_units.json) (--id-prefix  https://our-ror.example.edu/)
```
## Export data from Jschol
```
mysql --defaults-extra-file=~/.passwords/jschol_ro_pw.mysql -s -e "SELECT id, name, type, status FROM units;" > /tmp/all_units.tsv
mysql --defaults-extra-file=~/.passwords/jschol_ro_pw.mysql -s -e "SELECT unit_id, ancestor_unit as parent FROM unit_hier WHERE is_direct = 1;" > /tmp/all_relations.tsv
```
