from pathlib import Path
from ruamel.yaml import YAML
from ruamel.yaml.comments import CommentedMap, CommentedSeq
from toc_prepare import toc_to_mkdocs_nav

yaml = YAML(typ='rt')
yaml.preserve_quotes = True
yaml.indent(mapping=2, sequence=4, offset=2)

# ========== Load paths and configurations ================== #
main_docs_path = Path("/root/stormbpmn_doc_project/stormbpmn-docs")
main_mkdocs_path = Path("/root/stormbpmn_doc_project/mkdocs")
mkdocs_config_path = Path(f"{main_mkdocs_path}/mkdocs.yml")
toc_docs_path =  Path(f"{main_docs_path}/toc.yaml")
test_conf = Path("./test_conf.yaml")
vars_path = Path(f"{main_docs_path}/vars.yaml")
custom_dir_path = Path("/root/stormbpmn_doc_project/stormbpmn-docs-styles")
global_vars_path = Path(f"{main_docs_path}/global_vars.yaml")


with mkdocs_config_path.open(encoding="utf-8") as f:
    config = yaml.load(f)

with toc_docs_path.open(encoding="utf-8") as f:
    toc = yaml.load(f)

with vars_path.open(encoding="utf-8") as f:
    vars = yaml.load(f)

with global_vars_path.open(encoding="utf-8") as f:
    global_vars = yaml.load(f)

config['nav'] = toc_to_mkdocs_nav(toc)
config['extra'] = vars
config['extra'].update(global_vars)
config['docs_dir'] = str(main_docs_path)
config['theme']['custom_dir'] = str(custom_dir_path)

with test_conf.open("w", encoding="utf-8") as f:
    yaml.dump(config, f)

print(toc)