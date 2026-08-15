from setuptools import find_packages, setup
import os

if __name__ == "__main__":
    here = os.path.abspath(os.path.dirname(__file__))

    with open(
        os.path.join(here, "README.md"), encoding="utf-8"
    ) as f:
        long_description = "\n" + f.read()

    with open(
        os.path.join(here, "requirements.txt"), encoding="utf-8"
    ) as f:
        install_requires = [line.strip() for line in f if line.strip() and not line.startswith('#')]

    setup(
        name="saphirapy",
        version='0.0.0+placeholder',
        author="Kwesi Rutledge",
        author_email="thesolitaryecrivain@gmail.com",
        url="https://github.com/WrenchRobotics/saphira",
        description="A python library to assist with the benchmarking and testing of Drake-based robotics systems.",
        long_description_content_type="text/markdown",
        long_description=long_description,
        install_requires=install_requires,
        keywords=["drake", "robotics", "testing", "logging"],
        classifiers=[
            "Programming Language :: Python :: 3",
        ],
        entry_points={
            "console_scripts": [
                "saphira-detect-production-map=saphirapy.detect_production_map:main",
                "saphira-restage-and-run-production=saphirapy.restage_and_run_production:main",
            ],
        },
    )