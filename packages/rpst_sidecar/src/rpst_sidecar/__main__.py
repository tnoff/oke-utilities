"""So `python -m jwks_publisher` invokes main()."""

import sys  # pragma: no cover

from .main import main  # pragma: no cover

if __name__ == "__main__":  # pragma: no cover
    sys.exit(main())
