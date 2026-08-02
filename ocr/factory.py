import config


def get_ocr_provider():
    provider = config.OCR_PROVIDER

    if provider == "tesseract":
        from .tesseract_ocr import TesseractOcr
        return TesseractOcr()

    if provider == "google":
        from .cloud_google import GoogleDocAiOcr
        return GoogleDocAiOcr(config.GOOGLE_DOCAI_PROCESSOR_ID)

    if provider == "textract":
        from .cloud_textract import TextractOcr
        return TextractOcr(config.AWS_REGION)

    if provider == "azure":
        from .cloud_azure import AzureReadOcr
        return AzureReadOcr(
            config.AZURE_FORM_RECOGNIZER_ENDPOINT, config.AZURE_FORM_RECOGNIZER_KEY
        )

    raise RuntimeError(
        f"Unknown OCR_PROVIDER '{provider}'. Use one of: tesseract, google, textract, azure."
    )
