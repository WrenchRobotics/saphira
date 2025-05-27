from setuptools import find_packages, setup
import codecs
import os

if __name__ == "__main__":
    here = os.path.abspath(os.path.dirname(__file__))

    with codecs.open(
        os.path.join(here, "README.md"), encoding="utf-8"
    ) as f:
        long_description = "\n" + f.read()

    setup(
        name="saphira_py",
        version='{{VERSION_PLACEHOLDER}}',
        author="Kwesi Rutledge",
        author_email="thesolitaryecrivain@gmail.com",
        url="https://github.com/WrenchRobotics/saphira",
        description="A python library to assist with the benchmarking and testing of Drake-based robotics systems.",
        long_description_content_type="text/markdown",
        long_description=long_description,
        # packages=find_packages(where='src/brom_drake'),
        install_requires=[
            'drake', 'meshcat',
            'loguru', 'matplotlib', 'numpy',
            'pycollada', 'trimesh',
        ],
        keywords=["drake", "robotics", "testing", "logging"],
        classifiers=[
            "Programming Language :: Python :: 3",
        ]
    )