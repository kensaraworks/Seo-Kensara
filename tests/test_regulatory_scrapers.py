"""Tests for the regulatory HTML scrapers."""
import pytest
from src.scrapers.regulatory_scrapers import (
    fetch_meity_gazette,
    fetch_meity_press_releases,
    fetch_cert_in_advisories,
)

@pytest.mark.asyncio
async def test_fetch_meity_gazette_mock(mocker):
    mock_html = """
    <html>
      <body>
        <table>
          <tr><th>No.</th><th>Title</th></tr>
          <tr><td>1</td><td><a href="/notifications/dpdpa-rules">Draft Digital Personal Data Protection Rules 2025</a></td></tr>
        </table>
      </body>
    </html>
    """
    mock_get = mocker.patch("httpx.AsyncClient.get")
    mock_response = mocker.MagicMock()
    mock_response.status_code = 200
    mock_response.text = mock_html
    mock_get.return_value = mock_response

    items = await fetch_meity_gazette()
    assert len(items) == 1
    assert items[0].title == "Draft Digital Personal Data Protection Rules 2025"
    assert "dpdpa-rules" in items[0].url
    assert items[0].source == "MeitY Gazette"

@pytest.mark.asyncio
async def test_fetch_meity_press_releases_mock(mocker):
    mock_html = """
    <html>
      <body>
        <div class="views-row">
          <a href="/press-release/guidelines">Press Release: DPDPA Guidelines issued by Ministry</a>
        </div>
      </body>
    </html>
    """
    mock_get = mocker.patch("httpx.AsyncClient.get")
    mock_response = mocker.MagicMock()
    mock_response.status_code = 200
    mock_response.text = mock_html
    mock_get.return_value = mock_response

    items = await fetch_meity_press_releases()
    assert len(items) == 1
    assert items[0].title == "Press Release: DPDPA Guidelines issued by Ministry"
    assert "guidelines" in items[0].url
    assert items[0].source == "MeitY Press Releases"

@pytest.mark.asyncio
async def test_fetch_cert_in_advisories_mock(mocker):
    mock_html = """
    <html>
      <body>
        <div class="advisory">
          <a href="s2cMainServlet?pageid=PUBADVISORY&id=102">CERT-In Advisory CIAD-2025-001: Data Breach vulnerability</a>
        </div>
      </body>
    </html>
    """
    mock_get = mocker.patch("httpx.AsyncClient.get")
    mock_response = mocker.MagicMock()
    mock_response.status_code = 200
    mock_response.text = mock_html
    mock_get.return_value = mock_response

    items = await fetch_cert_in_advisories()
    assert len(items) == 1
    assert "CIAD-2025-001" in items[0].title
    assert "PUBADVISORY" in items[0].url
    assert items[0].source == "CERT-In"
