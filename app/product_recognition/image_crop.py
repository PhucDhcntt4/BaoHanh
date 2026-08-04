from io import BytesIO

from PIL import Image, UnidentifiedImageError


def crop_product_region(
    image_bytes: bytes,
    bounding_box: list[int] | None,
    padding_ratio: float = 0.12,
) -> tuple[bytes, str, bool]:
    """Crop a 0..1000 [ymin, xmin, ymax, xmax] box with padding."""
    if not bounding_box or len(bounding_box) != 4:
        return image_bytes, "", False
    try:
        with Image.open(BytesIO(image_bytes)) as image:
            width, height = image.size
            y_min, x_min, y_max, x_max = [
                max(0, min(1000, int(value)))
                for value in bounding_box
            ]
            if x_max <= x_min or y_max <= y_min:
                return image_bytes, "", False

            left = x_min * width / 1000
            right = x_max * width / 1000
            top = y_min * height / 1000
            bottom = y_max * height / 1000
            padding_x = (right - left) * padding_ratio
            padding_y = (bottom - top) * padding_ratio
            left = max(0, int(left - padding_x))
            right = min(width, int(right + padding_x))
            top = max(0, int(top - padding_y))
            bottom = min(height, int(bottom + padding_y))

            crop_width = right - left
            crop_height = bottom - top
            if crop_width < 120 or crop_height < 120:
                return image_bytes, "", False
            if crop_width * crop_height >= width * height * 0.90:
                return image_bytes, "", False

            cropped = image.crop((left, top, right, bottom)).convert("RGB")
            output = BytesIO()
            cropped.save(output, format="JPEG", quality=90, optimize=True)
            return output.getvalue(), "image/jpeg", True
    except (UnidentifiedImageError, OSError, ValueError):
        return image_bytes, "", False
