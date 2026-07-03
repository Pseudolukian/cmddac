import sys
from pathlib import Path
sys.path.insert(0, str(Path(__file__).parent.parent))

from psd_handler.psd_handler import PSDHandler
from data_models.psd_config import PSDConfig

psd_handler = PSDHandler(output_dir="/root/out_test_storage")
out = psd_handler.render(PSDConfig(
    psd_path="/root/stormbpmn_project/src_storage/diagram.psd",
    base_layer="Account",
    Frames=["Account_btn","Account_email"],
    Focuses=["Account"],
    Crops=[]
))
print("Saved:", out)
