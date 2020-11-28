import abc
from abc import abstractmethod
from enum import Enum, unique
import platform
import distro
import yaml
import prettytable as pt
from pathlib import Path
import subprocess
import os
import shlex
import urllib.request
import time


class BaseError(Exception):
    pass


class NotSupportError(BaseError):
    pass


class InstallPkgError(BaseError):
    pass


class SubProcessError(InstallPkgError):
    pass


@unique
class Systems(Enum):
    DARWIN = "Darwin"
    LINUX = "Linux"


@unique
class LinuxDistributions(Enum):
    UBUNTU = "Ubuntu"
    CENTOS = "CentOS Linux"


@unique
class Pkgs(Enum):
    BREW = "brew_pkgs"
    APT = "apt_pkgs"
    DNF = "dnf_pkgs"


class SubProcess:
    @staticmethod
    def run_get_ret(cmd):
        return subprocess.run(cmd, stdout=subprocess.DEVNULL).returncode

    @staticmethod
    def run(cmd, *, shell=False, user_input=None):
        print(f"subprocess cmd: {cmd}")
        # redirect std.err to std.out
        # when set input in subprocess.run, the internal
        # stdin is subprocess.PIPE
        p = subprocess.run(cmd, shell=shell, input=user_input, encoding="utf-8")
        p.check_returncode()


class FileUtils:
    @staticmethod
    def check_and_create_dir(dir_path):
        """
        Create the dir if it does not exist
        p.mkdir(parents=True, exist_ok=True) may be a better solution.
        """
        p = Path(dir_path)
        if p.is_dir():
            # alreasdy exists
            return True

        # create the parent dir
        p.mkdir(parents=True, exist_ok=True)
        return False

    @staticmethod
    def create_dir(dir_path):
        p = Path(dir_path)
        p.mkdir(parents=True, exist_ok=True)

    @staticmethod
    def delete_file(file_path):
        if not os.path.isfile(file_path):
            return
        os.unlink(file_path)

    @staticmethod
    def delete_link(link_path):
        if not os.path.islink(link_path):
            return
        os.unlink(link_path)


class PkgMgrAgent(abc.ABC):
    def check_installed(self, pkg):
        """
        Use which to check if the pkg installed
        """
        cmd = ("which", pkg)
        ret_code = SubProcess.run_get_ret(cmd)
        return True if ret_code == 0 else False

    @abstractmethod
    def install(self, pkg):
        raise NotImplementedError("0.0")


class DarwinAgent(PkgMgrAgent):
    def __init__(self, update_brew=False):

        # install homebrew
        self.brew = "brew"
        self.brew_cast = "brew cask"

        if not self.check_installed("brew"):
            print("install brew:")
            cmd = r'/usr/bin/ruby -e "$(curl -fsSL https://raw.githubusercontent.com/Homebrew/install/master/install)"'
            # \n means enter
            SubProcess.run(cmd, shell=True, user_input="\n")

        if update_brew:
            print("update brew:")
            SubProcess.run(shlex.split(f"{self.brew} update --force --verbose"))

    def install(self, pkg_info):
        bin_name = pkg_info["bin"]
        name = pkg_info["bin"]
        pkg = pkg_info["pkg"]
        tap = pkg_info["tap"]
        cask = pkg_info["cask"]

        print(f"install name:{name}, pkg:{pkg}, tap:{tap}")
        if bin_name and self.check_installed(bin_name):
            print(f"the {pkg} is installed already by checking the {bin_name}")
            return

        if tap:
            SubProcess.run(shlex.split(f"{self.brew} tap {tap}"))

        if "post_cmd" in pkg_info:
            post_cmd = pkg_info["post_cmd"]
            SubProcess.run(shlex.split(post_cmd))

        pkg_mgr = self.brew
        if cask:
            pkg_mgr = self.brew_cast

        SubProcess.run(shlex.split(f"{pkg_mgr} install {pkg}"))

    def __repr__(self):
        return "DarwinAgent"


class UbuntuAgent(PkgMgrAgent):
    def __init__(self, _):
        self.pkg_mgr = "apt"
        SubProcess.run(shlex.split(f"{self.pkg_mgr} update -y"))
        SubProcess.run(shlex.split(f"{self.pkg_mgr} install -y software-properties-common"))
        SubProcess.run(shlex.split(f"{self.pkg_mgr} update -y"))

    def install(self, pkg_info):
        bin_name = pkg_info["bin"]
        name = pkg_info["bin"]
        pkg = pkg_info["pkg"]
        add_repo = pkg_info["add-repo"]

        print(f"install name:{name}, pkg:{pkg}:, add_repo:{add_repo}")
        if bin_name and self.check_installed(bin_name):
            return

        if add_repo:
            SubProcess.run(shlex.split(f"add-apt-repository -y {add_repo}"))

        SubProcess.run(shlex.split(f"{self.pkg_mgr} install -y {pkg}"))

    def __repr__(self):
        return "UbuntuAgent"


class CentOSAgent(PkgMgrAgent):
    def __init__(self, distrib_ver):
        self.pkg_mgr = "apt"
        if distrib_ver[0] >= "8":
            self.pkg_mgr = "dnf"

        SubProcess.run(shlex.split(f"{self.pkg_mgr} update -y"))
        SubProcess.run(shlex.split(f"{self.pkg_mgr} install -y epel-release"))

    def install(self, pkg_info):
        bin_name = pkg_info["bin"]
        name = pkg_info["bin"]
        pkg = pkg_info["pkg"]

        print(f"install name:{name}, pkg:{pkg}:")
        if bin_name and self.check_installed(bin_name):
            return

        SubProcess.run(shlex.split(f"{self.pkg_mgr} install -y {pkg}"))

    def __repr__(self):
        return "CentOSAgent"


class GitAgent:
    @classmethod
    def clone(cls, repo, dst_path, *, check=True):
        if check and FileUtils.check_and_create_dir(dst_path):
            return
        cmd = ("git", "clone", "--recurse-submodules", repo, dst_path)
        SubProcess.run(cmd)


class ConfigMgr:
    def __init__(self, config_path="config.yaml"):
        with open(config_path, "r") as fd:
            self.config = yaml.safe_load(fd)

    def pkgs(self, system, distrib_name, distrib_ver):
        if system == Systems.DARWIN.value:
            return self.config[Pkgs.BREW.value]
        else:
            if distrib_name == LinuxDistributions.UBUNTU.value:
                return self.config[Pkgs.APT.value]
            else:
                return self.config[Pkgs.DNF.value]

    @property
    def dotfiles(self):
        return self.config["dotfiles"]

    @property
    def git_repos(self):
        return self.config["git_repos"]


class Installer:
    def __init__(
        self,
        *,
        home,
        system,
        distrib_name,
        distrib_ver,
        git_agent: GitAgent,
        pkg_install_agent: PkgMgrAgent,
        config_mgr: ConfigMgr,
    ):

        self.home = home
        self.system = system
        self.distrib_name = distrib_name
        self.distrib_ver = distrib_ver
        self.cwd = os.path.abspath(os.getcwd())
        self.git_agent = git_agent
        self.pkg_install_agent = pkg_install_agent
        self.config_mgr = config_mgr

    def __repr__(self):
        return "0.0"

    def all(self):
        print("test")

    def install_pkgs(self):
        pkgs = self.config_mgr.pkgs(self.system, self.distrib_name, self.distrib_ver)

        print("\nstart to install pkgs:")
        tb = pt.PrettyTable()
        tb.field_names = ["name", "bin", "pkg"]
        for pkg in pkgs:
            tb.add_row([pkg["name"], pkg["bin"], pkg["pkg"]])

        print(tb)
        time.sleep(3)

        for pkg in pkgs:
            self.pkg_install_agent.install(pkg)

    def clone_git_repos(self):

        git_repos = self.config_mgr.git_repos

        print("\nstart to clone git repos:")
        tb = pt.PrettyTable()
        tb.field_names = ["src", "dst"]
        src_dst_map = []
        for git_repo in git_repos:
            src = git_repo["src"]
            dst = os.path.join(self.home, git_repo["dst"])
            src_dst_map.append((src, dst))
            tb.add_row([src, dst])

        print(tb)
        time.sleep(3)

        for src, dst in src_dst_map:
            print(f"start to clone:{src} to {dst}")
            self.git_agent.clone(repo=src, dst_path=dst)

    def link_dotfiles(self):

        dotfiles = self.config_mgr.dotfiles

        print("\nstart to link dotfiles:")
        tb = pt.PrettyTable()
        tb.field_names = ["src", "dst"]
        src_dst_map = []
        for dotfile in dotfiles:
            src = os.path.join(self.cwd, dotfile["src"])
            dst = os.path.join(self.home, dotfile["dst"])
            src_dst_map.append((src, dst))
            tb.add_row([src, dst])

        print(tb)
        time.sleep(3)

        for src, dst in src_dst_map:
            dst_dirname = os.path.dirname(dst)
            FileUtils.create_dir(dst_dirname)
            FileUtils.delete_link(dst)
            FileUtils.delete_file(dst)
            print(f"start to link:{src} to {dst}")
            os.symlink(src, dst)

    def install_fonts(self):
        if self.system == Systems.DARWIN.value:
            return

        font_d = f"{Path.home()}/.local/share/fonts"
        font_config_d = f"{Path.home()}/.config/fontconfig/conf.d"

        FileUtils.create_dir(font_d)
        FileUtils.create_dir(font_config_d)

        urllib.request.urlretrieve(
            "https://github.com/powerline/powerline/raw/develop/font/PowerlineSymbols.otf",
            f"{font_d}/PowerlineSymbols.otf",
        )

        SubProcess.run(shlex.split(f"fc-cache -vf {font_d}"))

        urllib.request.urlretrieve(
            "https://github.com/powerline/powerline/raw/develop/font/10-powerline-symbols.conf",
            f"{font_config_d}/10-powerline-symbols.conf",
        )

    def install_editor_plugins(self):
        # python plugin for neovim
        SubProcess.run(shlex.split("pip3 install neovim --upgrade"))

    def install_kubectl_plugins(self):
        if self.system is not Systems.DARWIN:
            return

        # context
        SubProcess.run(shlex.split("kubectl krew install ctx"))
        # namespace
        SubProcess.run(shlex.split("kubectl krew install ns"))


def get_agent(system, distrib_name, distrib_ver) -> PkgMgrAgent:
    if system == Systems.DARWIN.value:
        return DarwinAgent()
    else:
        if distrib_name == LinuxDistributions.UBUNTU.value:
            return UbuntuAgent(distrib_ver)
        else:
            return CentOSAgent(distrib_ver)


def check_supported(system, distrib_name):
    supported_system = tuple(system.value for system in Systems)
    supported_lix_distribution = tuple(dist.value for dist in LinuxDistributions)

    if system not in supported_system:
        raise NotSupportError(f"does not support {system} now")

    if system == "Linux":
        if distrib_name not in supported_lix_distribution:
            raise NotSupportError(f"does not support {distrib_name} now")


def main():
    # Darwin, Ubuntu
    system = platform.system()
    print(f"System is {system}")
    time.sleep(2)

    # ('Ubuntu', '18.04', 'bionic')
    # ('CentOS Linux', '8.1.1911', 'Core')
    distrib_name, distrib_ver, _ = distro.linux_distribution()
    if system == Systems.LINUX.value:
        print(f"Distribution is {distrib_name}:{distrib_ver}")
        time.sleep(2)

    check_supported(system, distrib_name)

    pkg_mgr_agent = get_agent(system, distrib_name, distrib_ver)
    print(f"pkg_mgr_agent is {pkg_mgr_agent}")
    time.sleep(2)

    installer = Installer(
        home=Path.home(),
        system=system,
        distrib_name=distrib_name,
        distrib_ver=distrib_ver,
        git_agent=GitAgent,
        pkg_install_agent=pkg_mgr_agent,
        config_mgr=ConfigMgr(),
    )

    installer.install_pkgs()
    installer.clone_git_repos()
    installer.link_dotfiles()
    installer.install_fonts()
    installer.install_editor_plugins()
    installer.install_kubectl_plugins()

    # change default shell (need admin)
    # sudo sh -c "echo $(which zsh) >> /etc/shells"
    # SubProcess.run(cmd="chsh -s $(which zsh)", shell=True)


if __name__ == "__main__":
    main()
