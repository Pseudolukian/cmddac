from pathlib import Path
from ruamel.yaml import YAML
from ruamel.yaml.comments import CommentedMap, CommentedSeq
from toc_prepare import toc_to_mkdocs_nav
import sys

yaml = YAML(typ='rt')
yaml.preserve_quotes = True
yaml.indent(mapping=2, sequence=4, offset=2)

# ========== Load paths and configurations ================== #
# Defoult paths: 
# - main_docs_path   = Path("/root/stormbpmn_doc_project/stormbpmn-docs")
# - main_mkdocs_path = Path("/root/stormbpmn_doc_project/mkdocs")
# - custom_dir_path  = Path("/root/stormbpmn_doc_project/stormbpmn-docs-styles")
main_docs_path       = Path(sys.argv[1])
main_mkdocs_path     = Path(sys.argv[2])
custom_dir_path      = Path(sys.argv[3])
global_vars_path     = Path(f"{main_docs_path}/global_vars.yaml")
mkdocs_config_path   = Path(f"{main_mkdocs_path}/mkdocs.yml")
toc_docs_path        = Path(f"{main_docs_path}/toc.yaml")
out_conf             = Path(f"{main_mkdocs_path}/out_conf.yaml")
vars_path            = Path(f"{main_docs_path}/vars.yaml")

# ========== Load configurations from files ================== #
with mkdocs_config_path.open(encoding="utf-8") as f:
    config = yaml.load(f)

with toc_docs_path.open(encoding="utf-8") as f:
    toc = yaml.load(f)

with vars_path.open(encoding="utf-8") as f:
    vars = yaml.load(f)

with global_vars_path.open(encoding="utf-8") as f:
    global_vars = yaml.load(f)

# ========== Update mkdocs configuration ================== #
config['nav']                 = toc_to_mkdocs_nav(toc)
config['extra']               = vars
config['docs_dir']            = str(main_docs_path)
config['theme']['custom_dir'] = str(custom_dir_path)
config['extra'].update(global_vars)

# ========== Write the updated configuration back to a file ================== #
with out_conf.open("w", encoding="utf-8") as f:
    yaml.dump(config, f)