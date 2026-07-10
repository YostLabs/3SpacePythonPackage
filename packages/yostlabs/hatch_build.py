from pathlib import Path

from hatchling.metadata.plugin.interface import MetadataHookInterface


class ReadmeMetadataHook(MetadataHookInterface):
    PLUGIN_NAME = "custom"

    def update(self, metadata):
        print("Meta data hook!!")
        readme_path = (Path(self.root) / "../../README.md").resolve()

        if not readme_path.exists():
            raise FileNotFoundError(f"Expected README at {readme_path}, but it doesn't exist")

        metadata["readme"] = {
            "content-type": "text/markdown",
            "text": readme_path.read_text(encoding="utf-8"),
        }