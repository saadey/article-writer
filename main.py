"""Run the pipeline: python main.py --help"""

import sys
import warnings

# macOS system Python ships LibreSSL, which makes urllib3 print a harmless warning.
warnings.filterwarnings("ignore", message=".*urllib3 v2 only supports OpenSSL.*")

from article_pipeline.cli import main  # noqa: E402

if __name__ == "__main__":
    sys.exit(main())
