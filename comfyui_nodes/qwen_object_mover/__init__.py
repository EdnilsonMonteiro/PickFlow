import numpy as np
import torch
from PIL import Image, ImageDraw

_COLORS = {
    "red": (255, 0, 0),
    "green": (0, 210, 0),
    "blue": (0, 120, 255),
    "yellow": (255, 220, 0),
}


class DrawMoveBoxes:
    """Draws a source box and a target box on an image (pixel coordinates)."""

    @classmethod
    def INPUT_TYPES(cls):
        box = {"default": 0, "min": 0, "max": 16384, "step": 1}
        return {
            "required": {
                "image": ("IMAGE",),
                "source_x": ("INT", box),
                "source_y": ("INT", box),
                "source_width": ("INT", {"default": 160, "min": 1, "max": 16384, "step": 1}),
                "source_height": ("INT", {"default": 160, "min": 1, "max": 16384, "step": 1}),
                "target_x": ("INT", {"default": 320, "min": 0, "max": 16384, "step": 1}),
                "target_y": ("INT", box),
                "target_width": ("INT", {"default": 160, "min": 1, "max": 16384, "step": 1}),
                "target_height": ("INT", {"default": 160, "min": 1, "max": 16384, "step": 1}),
                "thickness": ("INT", {"default": 8, "min": 1, "max": 128, "step": 1}),
                "source_color": (list(_COLORS.keys()), {"default": "red"}),
                "target_color": (list(_COLORS.keys()), {"default": "green"}),
            }
        }

    RETURN_TYPES = ("IMAGE",)
    FUNCTION = "draw"
    CATEGORY = "image/object-mover"

    def draw(self, image, source_x, source_y, source_width, source_height,
             target_x, target_y, target_width, target_height, thickness,
             source_color="red", target_color="green"):
        results = []
        for img in image:
            arr = (img.detach().cpu().numpy() * 255.0).astype(np.uint8)
            pil = Image.fromarray(arr[..., :3], mode="RGB")
            d = ImageDraw.Draw(pil)
            sc, tc = _COLORS.get(source_color, (255, 0, 0)), _COLORS.get(target_color, (0, 210, 0))
            d.rectangle([source_x, source_y, source_x + source_width, source_y + source_height], outline=sc, width=int(thickness))
            d.rectangle([target_x, target_y, target_x + target_width, target_y + target_height], outline=tc, width=int(thickness))
            results.append(np.asarray(pil).astype(np.float32) / 255.0)
        return (torch.from_numpy(np.stack(results)),)


class MoveBoxes:
    """Interactive red/green move-box editor (one image)."""

    @classmethod
    def INPUT_TYPES(cls):
        f = {"min": 0.0, "max": 1.0, "step": 0.001}
        return {
            "required": {
                "image": ("IMAGE",),
                "src_x": ("FLOAT", {"default": 0.10, **f}),
                "src_y": ("FLOAT", {"default": 0.30, **f}),
                "src_w": ("FLOAT", {"default": 0.25, **f}),
                "src_h": ("FLOAT", {"default": 0.30, **f}),
                "tgt_x": ("FLOAT", {"default": 0.60, **f}),
                "tgt_y": ("FLOAT", {"default": 0.30, **f}),
                "tgt_w": ("FLOAT", {"default": 0.25, **f}),
                "tgt_h": ("FLOAT", {"default": 0.30, **f}),
                "thickness": ("INT", {"default": 8, "min": 1, "max": 128, "step": 1}),
                "src_color": (list(_COLORS.keys()), {"default": "red"}),
                "tgt_color": (list(_COLORS.keys()), {"default": "green"}),
            }
        }

    RETURN_TYPES = ("IMAGE",)
    FUNCTION = "draw"
    CATEGORY = "image/object-mover"

    def draw(self, image, src_x, src_y, src_w, src_h, tgt_x, tgt_y, tgt_w, tgt_h,
             thickness, src_color="red", tgt_color="green"):
        results = []
        for img in image:
            arr = (img.detach().cpu().numpy() * 255.0).astype(np.uint8)
            pil = Image.fromarray(arr[..., :3], mode="RGB")
            W, H = pil.size
            d = ImageDraw.Draw(pil)
            sc, tc = _COLORS.get(src_color, (255, 0, 0)), _COLORS.get(tgt_color, (0, 210, 0))
            d.rectangle([src_x * W, src_y * H, (src_x + src_w) * W, (src_y + src_h) * H], outline=sc, width=int(thickness))
            d.rectangle([tgt_x * W, tgt_y * H, (tgt_x + tgt_w) * W, (tgt_y + tgt_h) * H], outline=tc, width=int(thickness))
            results.append(np.asarray(pil).astype(np.float32) / 255.0)
        return (torch.from_numpy(np.stack(results)),)


class DrawBox:
    """Interactive single-box editor (draw one red or green box on the image)."""

    @classmethod
    def INPUT_TYPES(cls):
        f = {"min": 0.0, "max": 1.0, "step": 0.001}
        return {
            "required": {
                "image": ("IMAGE",),
                "box_x": ("FLOAT", {"default": 0.30, **f}),
                "box_y": ("FLOAT", {"default": 0.30, **f}),
                "box_w": ("FLOAT", {"default": 0.35, **f}),
                "box_h": ("FLOAT", {"default": 0.35, **f}),
                "thickness": ("INT", {"default": 8, "min": 1, "max": 128, "step": 1}),
                "color": (list(_COLORS.keys()), {"default": "red"}),
                "filled": ("BOOLEAN", {"default": True, "tooltip": "Pinta o quadrado por completo (melhor p/ substituir pelo prompt)."}),
            }
        }

    RETURN_TYPES = ("IMAGE",)
    FUNCTION = "draw"
    CATEGORY = "image/object-mover"

    def draw(self, image, box_x, box_y, box_w, box_h, thickness, color="red", filled=True):
        results = []
        for img in image:
            arr = (img.detach().cpu().numpy() * 255.0).astype(np.uint8)
            pil = Image.fromarray(arr[..., :3], mode="RGB")
            W, H = pil.size
            d = ImageDraw.Draw(pil)
            c = _COLORS.get(color, (255, 0, 0))
            rect = [box_x * W, box_y * H, (box_x + box_w) * W, (box_y + box_h) * H]
            if filled:
                d.rectangle(rect, fill=c)
            else:
                d.rectangle(rect, outline=c, width=int(thickness))
            results.append(np.asarray(pil).astype(np.float32) / 255.0)
        return (torch.from_numpy(np.stack(results)),)


NODE_CLASS_MAPPINGS = {"DrawMoveBoxes": DrawMoveBoxes, "MoveBoxes": MoveBoxes, "DrawBox": DrawBox}
NODE_DISPLAY_NAME_MAPPINGS = {
    "DrawMoveBoxes": "Draw Move Boxes (source/target)",
    "MoveBoxes": "Move Boxes (draw red+green on image)",
    "DrawBox": "Draw Box (red/green) on image",
}
WEB_DIRECTORY = "./web"
