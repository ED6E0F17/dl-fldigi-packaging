#!/usr/bin/python
# Copyright 2011 (C) Daniel Richman. License: GNU GPL 3

import sys
import os
import os.path
import subprocess
import shutil
import glob
import logging
import optparse
import re
import errno
import email.utils

logger = logging.getLogger("builder")

class Builder:
    def main(self):
        logging.basicConfig(level=logging.INFO,
                format="[%(asctime)s] %(message)s")

        self.get_options()

        if self.options["verbose"]:
            logging.getLogger().setLevel(level=logging.DEBUG)
        elif self.options["quiet"]:
            logging.getLogger().setLevel(level=logging.WARNING)

        try:
            self.null = open("/dev/null", "w")
            self.setup_build_dir()
        except:
            logger.exception("Error in setup")
            sys.exit(1)

        delay_error = False

        try:
            self.get_orig_tar()
            self.add_debian_dir()
            self.build()
            self.get_files()
        except:
            delay_error = True
            logger.exception("Error in build")

        try:
            self.null.close()
            self.clean_build_dir()
        except:
            logger.exception("Error whilst cleaning up")

        if delay_error:
            sys.exit(1)

    def get_options(self):
        parser = optparse.OptionParser(usage="%prog git-source [git-commit]")
        parser.add_option("-d", "--directory", dest="directory",
                help="disable INFO msgs", default="debian_build")
        parser.add_option("-q", "--quiet", dest="quiet",
                help="disable INFO msgs", action="store_true")
        parser.add_option("-v", "--verbose", dest="verbose",
                help="enable DEBUG info", action="store_true")
        parser.add_option("-o", "--output", dest="output",
                help="save the dl-fldigi deb here", default=".")
        parser.add_option("-s", "--source", dest="get_src", default=False,
                help="get the source debian files only", action="store_true")
        parser.add_option("-j", "--make-jobs", dest="make_jobs",
                help="pass -j to make for speedy builds")
        parser.add_option("-n", "--distro-name", dest="distro",
                help="distro to build for")

        (options, args) = parser.parse_args()
        self.options = options.__dict__

        if len(args) != 1 and len(args) != 2:
            parser.error("Expected single argument: dl-fldigi git location")
        if len(args) == 1:
            args.append(None)

        self.dl_fldigi_source = args[0]
        self.dl_fldigi_commit = args[1]

    def default_distro(self):
        distributor = self.cmd_output("lsb_release", "-si").strip()
        if distributor == "Debian":
            return "unstable"
        else:
            return self.cmd_output("lsb_release", "-sc").strip()

    def setup_build_dir(self):
        self.location = os.path.realpath(self.options["directory"])
        logger.debug("Build directory is " + self.location)

        if not re.match(r"^[a-zA-Z0-9_\-/]+$", self.location):
            raise Exception("Some build scripts don't like non a-zA-Z0-9-_ in "
                            "the path to the build directory; sorry :-(")

        self.clean_build_dir()
        os.mkdir(self.location)

    def clean_build_dir(self):
        try:
            os.stat(self.location)
        except OSError as e:
            if e.errno != errno.ENOENT:
                raise
        else:
            logger.debug("Cleaning " + self.location)
            shutil.rmtree(self.location)

    def cmd(self, *args, **kwargs):
        logger.debug("Executing: " + repr(args) + " " + repr(kwargs))

        if not self.options["verbose"]:
            if "stdout" not in kwargs:
                kwargs["stdout"] = self.null
            if "stderr" not in kwargs:
                kwargs["stderr"] = self.null

        if "cwd" not in kwargs:
            kwargs["cwd"] = self.location

        ret = subprocess.call(args, **kwargs)

        if ret != 0:
            raise Exception("subprocess error exited " + repr(args))

    def loc(self, *args):
        return os.path.join(self.location, *args)

    def cmd_output(self, *args, **kwargs):
        # Only works for small outputs

        (r, w) = os.pipe()
        read = os.fdopen(r)

        kwargs["stdout"] = w

        self.cmd(*args, **kwargs)
        os.close(w)

        line = read.read().strip()
        read.close()

        return line

    def get_orig_tar(self):
        g = self.loc("git-tmp")

        self.cmd("git", "clone", self.dl_fldigi_source, g, cwd=None)
        if self.dl_fldigi_commit:
            self.cmd("git", "checkout", self.dl_fldigi_commit, cwd=g)
        self.cmd("git", "submodule", "init", cwd=g)
        self.cmd("git", "submodule", "update", cwd=g)

        self.cmd("autoreconf", "-vfi", cwd=g)
        self.cmd("./configure", cwd=g)
        self.cmd("make", "dist", cwd=g)

        search = glob.glob(self.loc("git-tmp", "dl-fldigi-*.tar.gz"))
        assert len(search) == 1
        distname = os.path.basename(search[0])
        assert distname.startswith("dl-fldigi-")
        assert distname.endswith(".tar.gz")
        self.version = distname[len("dl-fldigi-"):-len(".tar.gz")]
        logger.info("Version is " + self.version)

        line = self.cmd_output("git", "log", "--oneline", "-1", cwd=g)

        self.git = line.split()[0]
        assert len(self.git) == 7

        logger.info("Git commit is " + self.git)

        self.origname = "dl-fldigi_" + self.version + ".orig.tar.gz"
        logger.info("Orig tarball is " + self.origname)

        shutil.copy(self.loc("git-tmp", distname),
                    self.loc(self.origname))
        shutil.rmtree(self.loc("git-tmp"))

    def add_debian_dir(self):
        self.debsrc = "dl-fldigi-" + self.version + "-" + self.git
        t = self.loc(self.debsrc)

        os.mkdir(t)
        self.cmd("tar", "xfC", self.loc(self.origname), t,
                 "--strip-components=1")

        script_loc = os.path.dirname(os.path.realpath(__file__))
        debian = os.path.join(script_loc, "debian")
        shutil.copytree(debian, self.loc(self.debsrc, "debian"))

        changelog_file = self.loc(self.debsrc, "debian", "changelog")

        with open(changelog_file) as f:
            changelog = f.read()

        distro = self.options["distro"]
        if not distro:
            distro = self.default_distro()

        changelog = changelog.format(version=self.version + "-" + self.git,
                                     distro=distro, commit=self.git,
                                     date=email.utils.formatdate())

        with open(changelog_file, "w") as f:
            f.write(changelog)

    def build(self):
        args = []
        if self.options["make_jobs"]:
            args.append("-j" + self.options["make_jobs"])

        if self.options["get_src"]:
            args.append("-S")

        self.cmd("debuild", "-uc", "-us", *args, cwd=self.loc(self.debsrc))

    def get_files(self):
        prefix = "dl-fldigi_" + self.version + "-" + self.git

        if self.options["get_src"]:
            files = [prefix + ".orig.tar.gz", prefix + ".debian.tar.gz",
                     prefix + ".dsc"]
            assert files[0] == self.origname

            for fn in files:
                logger.info("Copying " + fn)
                shutil.copy(self.loc(fn), self.options["output"])
        else:
            name = prefix + "_*.deb"
            search = glob.glob(self.loc(name))
            assert len(search) == 1
            deb = search[0]

            logger.info("Copying output deb " + os.path.basename(deb))
            shutil.copy(deb, self.options["output"])


if __name__ == "__main__":
    Builder().main()
