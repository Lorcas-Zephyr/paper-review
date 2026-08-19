from typing import List, Tuple
import cv2
import numpy as np


def to_gray(img: np.ndarray) -> np.ndarray:
    if len(img.shape) == 2:
        return img
    return cv2.cvtColor(img, cv2.COLOR_BGR2GRAY)


def binarize(gray: np.ndarray) -> np.ndarray:
    return cv2.adaptiveThreshold(gray, 255, cv2.ADAPTIVE_THRESH_GAUSSIAN_C, cv2.THRESH_BINARY, 35, 15)


def find_contours(binary: np.ndarray) -> List[np.ndarray]:
    contours, _ = cv2.findContours(binary, cv2.RETR_EXTERNAL, cv2.CHAIN_APPROX_SIMPLE)
    return contours


def contour_bboxes(contours: List[np.ndarray], min_area: int = 200) -> List[Tuple[int, int, int, int]]:
    bboxes = []
    for c in contours:
        x, y, w, h = cv2.boundingRect(c)
        if w * h >= min_area:
            bboxes.append((x, y, x + w, y + h))
    return bboxes


def detect_text_lines(gray: np.ndarray) -> List[Tuple[int, int, int, int]]:
    kernel = cv2.getStructuringElement(cv2.MORPH_RECT, (25, 3))
    morph = cv2.morphologyEx(gray, cv2.MORPH_CLOSE, kernel)
    _, th = cv2.threshold(morph, 0, 255, cv2.THRESH_BINARY + cv2.THRESH_OTSU)
    contours = find_contours(th)
    return contour_bboxes(contours, min_area=300)
