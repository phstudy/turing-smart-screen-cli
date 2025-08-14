#!/usr/bin/env python3

import os
import argparse
import requests
from PIL import Image
from io import BytesIO
import random


def get_person_bbox(asset_id: str, person_id: str, token: str, api_url: str = None):
    """Get bounding box for specific person in the asset"""
    url = f"{api_url}/assets/{asset_id}"
    headers = {"x-api-key": token}
    resp = requests.get(url, headers=headers)
    resp.raise_for_status()
    data = resp.json()
    
    print(f"DEBUG: Asset data keys: {list(data.keys())}")
    
    # Look for face detection data in different possible locations
    people = data.get("people", [])
    print(f"DEBUG: Found {len(people)} people in asset")
    
    # Check people array for matching person
    for person in people:
        if person.get("id") == person_id:
            faces = person.get("faces", [])
            print(f"DEBUG: Found {len(faces)} faces for person")
            if faces:
                # Use the first face if multiple
                face = faces[0]
                print(f"DEBUG: Face data: {face}")
                
                # Check if we have the correct bounding box fields
                if all(key in face for key in ['boundingBoxX1', 'boundingBoxY1', 'boundingBoxX2', 'boundingBoxY2']):
                    return {
                        "x1": face["boundingBoxX1"],
                        "y1": face["boundingBoxY1"], 
                        "x2": face["boundingBoxX2"],
                        "y2": face["boundingBoxY2"],
                        "absolute": True,  # Mark as absolute coordinates
                        "face_data": face  # Include face data for scaling
                    }
    
    return None


def get_person_bbox_alternative(asset_id: str, person_id: str, token: str, api_url: str = None):
    """Alternative method using faces endpoint"""
    try:
        url = f"{api_url}/faces"
        headers = {"x-api-key": token}
        params = {"id": asset_id}
        resp = requests.get(url, headers=headers, params=params)
        resp.raise_for_status()
        faces_data = resp.json()
        
        print(f"DEBUG: Faces endpoint response: {faces_data}")
        
        if isinstance(faces_data, list):
            for face in faces_data:
                if face.get("person", {}).get("id") == person_id:
                    bbox = face.get("boundingBox")
                    if bbox:
                        return {
                            "x1": bbox["x1"],
                            "y1": bbox["y1"], 
                            "x2": bbox["x2"],
                            "y2": bbox["y2"]
                        }
    except Exception as e:
        print(f"DEBUG: Faces endpoint failed: {e}")
    
    return None


def crop_around_person(image: Image.Image, bbox: dict, target_width=1920, target_height=480) -> Image.Image:
    width, height = image.size
    print(f"DEBUG: Processing face-based crop, original image size: {width}x{height}")
    
    # Step 1: Get face coordinates
    if bbox.get("absolute", False):
        x1, y1, x2, y2 = bbox["x1"], bbox["y1"], bbox["x2"], bbox["y2"]
    else:
        x1 = int(bbox["x1"] * width)
        y1 = int(bbox["y1"] * height)
        x2 = int(bbox["x2"] * width)
        y2 = int(bbox["y2"] * height)
    
    face_height = y2 - y1
    face_center_x = (x1 + x2) // 2
    face_center_y = (y1 + y2) // 2
    print(f"DEBUG: Face bbox: ({x1}, {y1}, {x2}, {y2})")
    print(f"DEBUG: Face height: {face_height}, Face center: ({face_center_x}, {face_center_y})")
    
    # Step 2: Crop from original image - face height with balanced left/right, centered on face
    crop_top = max(0, face_center_y - face_height // 2)
    crop_bottom = min(height, crop_top + face_height)
    # Adjust if we can't get full face height
    crop_top = max(0, crop_bottom - face_height)
    
    # Calculate left/right crop to center the face - use longer distance but stay within bounds
    left_distance = face_center_x  # Distance from face center to left edge
    right_distance = width - face_center_x  # Distance from face center to right edge
    half_width = max(left_distance, right_distance)  # Use the longer distance
    
    # Calculate crop boundaries within image bounds
    crop_left = max(0, face_center_x - half_width)
    crop_right = min(width, face_center_x + half_width)
    
    cropped_region = image.crop((crop_left, crop_top, crop_right, crop_bottom))
    crop_width, crop_height = cropped_region.size
    print(f"DEBUG: Cropped region: {crop_width}x{crop_height}")
    
    # Step 3: Scale to height 480px
    scale_factor = target_height / crop_height
    new_width = int(crop_width * scale_factor)
    new_height = target_height
    
    scaled_image = cropped_region.resize((new_width, new_height), Image.Resampling.LANCZOS)
    print(f"DEBUG: Scaled to: {new_width}x{new_height}")
    
    # Handle width constraints
    if new_width > target_width:
        # Calculate where face center would be in scaled image
        scaled_face_center_x = int((face_center_x - crop_left) * scale_factor)
        
        # Center crop around face center
        crop_start_x = max(0, min(scaled_face_center_x - target_width // 2, new_width - target_width))
        result = scaled_image.crop((crop_start_x, 0, crop_start_x + target_width, new_height))
        print(f"DEBUG: Face-centered crop to {target_width}x{new_height}, face center at {scaled_face_center_x}")
    else:
        # Pad with black
        result = Image.new('RGBA', (target_width, target_height), (0, 0, 0, 255))
        paste_x = (target_width - new_width) // 2
        result.paste(scaled_image, (paste_x, 0))
        print(f"DEBUG: Padded to {target_width}x{target_height}")
    
    # Rotate to portrait before returning
    result = result.rotate(270, expand=True)
    return result


def center_crop(image: Image.Image, target_width=1920, target_height=480) -> Image.Image:
    """Always crop to 1920x480 landscape, then rotate to 480x1920 portrait before saving."""
    width, height = image.size
    print(f"DEBUG: Forcing landscape center crop 1920x480, original image size: {width}x{height}")
    cx = width // 2
    cy = height // 2
    crop_width, crop_height = target_width, target_height
    left = max(0, cx - crop_width // 2)
    top = max(0, cy - crop_height // 2)
    right = min(left + crop_width, width)
    bottom = min(top + crop_height, height)
    left = max(0, right - crop_width)
    top = max(0, bottom - crop_height)
    cropped = image.crop((left, top, right, bottom))
    result = Image.new('RGBA', (crop_width, crop_height), (0, 0, 0, 255))
    crop_w, crop_h = cropped.size
    paste_x = (crop_width - crop_w) // 2
    paste_y = (crop_height - crop_h) // 2
    result.paste(cropped, (paste_x, paste_y))
    # Rotate to portrait before returning
    result = result.rotate(90, expand=True)
    return result


def fetch_random_asset_for_person(person_id: str, token: str, api_url: str = None):
    url = f"{api_url}/search/metadata"
    headers = {
        "Content-Type": "application/json",
        "x-api-key": token
    }
    payload = {"personIds": [person_id], "size": 1000}
    resp = requests.post(url, headers=headers, json=payload)
    resp.raise_for_status()
    data = resp.json()
    assets = data.get("assets").get("items", [])

    asset_ids = [asset['id'] for asset in assets if 'id' in asset]
    return random.choice(asset_ids) if asset_ids else None


def download_and_crop(asset_id: str, person_id: str, token: str, output_path: str, debug: bool = False, api_url: str = None):
    # Download image
    url = f"{api_url}/assets/{asset_id}/original"
    headers = {"x-api-key": token}
    resp = requests.get(url, headers=headers)
    resp.raise_for_status()
    
    # Save original image bytes only in debug mode
    if debug:
        original_output_path = output_path.replace("random.png", "original.png")
        with open(original_output_path, 'wb') as f:
            f.write(resp.content)
        print(f"Saved original: {original_output_path}")
    
    # Load original image without format conversion first
    original_image = Image.open(BytesIO(resp.content))
    
    # Check if image needs rotation based on EXIF
    try:
        exif = original_image._getexif()
        orientation = 1
        if exif is not None:
            orientation = exif.get(274, 1)  # 274 is the orientation tag
        print(f"DEBUG: EXIF orientation: {orientation}")
        
        # Apply same orientation correction that face detection would have used
        if orientation == 3:
            corrected_image = original_image.rotate(180, expand=True)
        elif orientation == 6:
            corrected_image = original_image.rotate(270, expand=True)
        elif orientation == 8:
            corrected_image = original_image.rotate(90, expand=True)
        else:
            corrected_image = original_image
            
        print(f"DEBUG: Original size: {original_image.size}, Corrected size: {corrected_image.size}")
    except:
        corrected_image = original_image
        print("DEBUG: No EXIF orientation data")
    
    # Try to get person bounding box
    bbox = get_person_bbox(asset_id, person_id, token, api_url)
    
    # If first method fails, try alternative
    if not bbox:
        print("DEBUG: Trying alternative faces endpoint")
        bbox = get_person_bbox_alternative(asset_id, person_id, token, api_url)
    
    if bbox:
        print(f"Found person detection, cropping around person")
        
        # Save face region from orientation-corrected image
        width, height = corrected_image.size
        print(f"DEBUG: Corrected image size: {width}x{height}")
        print(f"DEBUG: Bounding box: {bbox}")
        
        if bbox.get("absolute", False):
            # Get the face detection image dimensions from the face data
            face_data = bbox["face_data"]
            
            if face_data and 'imageWidth' in face_data and 'imageHeight' in face_data:
                face_img_width = face_data['imageWidth']
                face_img_height = face_data['imageHeight']
                scale_x = width / face_img_width
                scale_y = height / face_img_height
                
                print(f"DEBUG: Face detection image size: {face_img_width}x{face_img_height}")
                print(f"DEBUG: Scale factors: x={scale_x:.3f}, y={scale_y:.3f}")
                
                # Scale the coordinates
                x1 = int(bbox["x1"] * scale_x)
                y1 = int(bbox["y1"] * scale_y)
                x2 = int(bbox["x2"] * scale_x)
                y2 = int(bbox["y2"] * scale_y)
                # Use face detection orientation for cropping
                is_landscape_face = face_img_width > face_img_height
            else:
                # Fallback to original coordinates
                x1, y1, x2, y2 = bbox["x1"], bbox["y1"], bbox["x2"], bbox["y2"]
                is_landscape_face = width > height
        else:
            # Convert relative coordinates to absolute
            x1 = int(bbox["x1"] * width)
            y1 = int(bbox["y1"] * height)
            x2 = int(bbox["x2"] * width)
            y2 = int(bbox["y2"] * height)
            is_landscape_face = width > height
        
        # Calculate padding based on face size
        padding = max(y2 - y1, x2 - x1)
        face_left = max(0, x1 - padding)
        face_top = max(0, y1 - padding)
        face_right = min(width, x2 + padding)
        face_bottom = min(height, y2 + padding)
        
        face_crop = corrected_image.crop((face_left, face_top, face_right, face_bottom))
        face_output_path = output_path.replace("random.png", "face.png")
        face_crop.save(face_output_path)
        print(f"Saved face: {face_output_path}")
        
        # Update bbox with scaled coordinates for full image processing
        scaled_bbox = {
            "x1": face_left,
            "y1": face_top,
            "x2": face_right, 
            "y2": face_bottom,
            "absolute": True,
            "is_landscape_face": is_landscape_face
        }

    # Convert to RGBA for processing
    image = corrected_image.convert("RGBA")

    if bbox:
        # Continue with full image crop using scaled coordinates and face orientation
        cropped = crop_around_person(image, scaled_bbox)
    else:
        print(f"No person detection found, using center crop")
        cropped = center_crop(image)
    
    cropped.save(output_path)


def process_person(person_id: str, token: str, output_dir: str, debug: bool = False, api_url: str = None):
    os.makedirs(output_dir, exist_ok=True)
    print(f"Processing person: {person_id}")   
   
    asset_id = fetch_random_asset_for_person(person_id, token, api_url)
    if not asset_id:
        print("No assets found for person.")
        return

    output_path = os.path.join(output_dir, f"random.png")
    try:
        download_and_crop(asset_id, person_id, token, output_path, debug, api_url)
        print(f"Saved: {output_path}")
    except Exception as e:
        print(f"Failed to process asset {asset_id}: {e}")


def main():
    parser = argparse.ArgumentParser(description="Center crop Immich person images to 480x1920")
    parser.add_argument("--token", required=True, help="Immich x-api-key")
    parser.add_argument("--output", default=".", help="Output directory")
    parser.add_argument("--debug", action="store_true", help="Save original and face detection images")
    parser.add_argument("--api-url", default="http://100.71.170.123:2283/api", help="Immich API URL")
    parser.add_argument("--person-id", required=True, help="Person ID to fetch photos for")
    args = parser.parse_args()

    process_person(args.person_id, args.token, os.path.join(args.output), args.debug, args.api_url)


if __name__ == "__main__":
    main()