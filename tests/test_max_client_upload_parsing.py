from app.services.max_client import _extract_upload_token, _extract_upload_url


def test_extract_upload_url_from_standard_response():
    payload = {"url": "https://iu.oneme.ru/uploadImage?x=1"}
    assert _extract_upload_url(payload) == "https://iu.oneme.ru/uploadImage?x=1"


def test_extract_upload_token_from_nested_photos_response():
    payload = {
        "photos": {
            "photo-id": {
                "token": "nested-photo-token",
            }
        }
    }
    assert _extract_upload_token(payload) == "nested-photo-token"


def test_extract_upload_token_from_root_response():
    payload = {"fileId": 123, "token": "root-token"}
    assert _extract_upload_token(payload) == "root-token"
