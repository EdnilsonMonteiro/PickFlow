from pathlib import Path

from pickflow import install_nodes as inn


def test_installs_missing_nodes_and_keeps_changed_files_unless_told(tmp_path):
    dest = tmp_path / "custom_nodes"
    dest.mkdir()
    assert set(inn.status(dest).values()) == {"falta"}
    inn.install(dest)
    assert set(inn.status(dest).values()) == {"ok"}
    assert (dest / "qwen_object_mover" / "web" / "move_boxes.js").is_file()
    (dest / "viggle_turbo.py").write_text("# meu", encoding="utf-8")
    assert inn.install(dest)["viggle_turbo.py"] == "diferente"           # não sobrescreve sozinho
    assert (dest / "viggle_turbo.py").read_text(encoding="utf-8") == "# meu"
    assert inn.install(dest, overwrite=True)["viggle_turbo.py"] == "instalado"


def test_finds_custom_nodes_from_comfyui_folder(tmp_path):
    (tmp_path / "ComfyUI" / "custom_nodes").mkdir(parents=True)
    assert inn.find_custom_nodes(str(tmp_path / "ComfyUI"))[0].name == "custom_nodes"
    assert inn.find_custom_nodes(str(tmp_path / "ComfyUI" / "custom_nodes"))[0].parent.name == "ComfyUI"
