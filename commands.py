import traceback
import inspect
import re
import types
import typing
import requests

from util import *
from config import *
from history import history
from log import *

try:
    conf = cli_config(required_fields=("server_ip", "server_port"))
except Exception as e:
    print("commands.py: cli_config:", e)
    traceback.print_exc()
    sys.exit(1)


################################################################################
#                                                                              #
#   Base class of CLI commands                                                 #
#                                                                              #
################################################################################

class CommandBase():
    """
    Base class of all commands for the mreg client. It provide functions which uses insight to
    auto-generate documentation and cli-info.

    To add a new option to the command create a opt_<option-name> method which takes a list of
    arguments as input.
    """

    def __init__(self):
        pass

    @staticmethod
    def _is_option(value):
        """Identify an option method"""
        if isinstance(value, types.MethodType):
            if re.match("^opt_.*$", value.__name__):
                return True
        return False

    def _option_methods(self) -> typing.List[typing.Tuple[str, typing.Callable]]:
        """Return all option methods of self"""
        # getmembers returns a list of tuples with: (<method name>, <method object>)
        return inspect.getmembers(self, predicate=self._is_option)

    def help(self) -> str:
        """Generate a help message of the command (self) and all its options"""
        help_str = "{}\n\nOptions:\n".format(inspect.getdoc(self))
        for method in self._option_methods():
            assert isinstance(method[1], types.MethodType)
            for line in inspect.getdoc(method[1]).splitlines(keepends=False):
                help_str += "   {}\n".format(line)
        return help_str

    def options(self) -> typing.List[str]:
        """Returns all options of this command, identified by function prefix "opt_\""""
        options = []
        for method in self._option_methods():
            options.append(method[0].split('_', maxsplit=1)[1])
        return options

    def opt_help(self, opt: str) -> None:
        """
        help <option>
            Return the documentation for the option.
        """
        for method in self._option_methods():
            if method[0] == "opt_" + opt:
                assert isinstance(method[1], types.MethodType)
                print(inspect.getdoc(method[1]))
                return
        print("No documentation of \"{}\"".format(opt))

    def method(self, opt: str) -> typing.Callable:
        """Returns the actual option method from a user-friendly option name."""
        for method in self._option_methods():
            if method[0] == "opt_" + opt:
                assert isinstance(method[1], types.MethodType)
                return method[1]
        cli_error("unknown option: {}".format(opt))


################################################################################
#                                                                              #
#   Command classes                                                            #
#                                                                              #
################################################################################


class History(CommandBase):
    """
    Show history or redo/undo actions.
    """

    def opt_print(self, args: typing.List[str]):
        """
        print
            Print the history.
        """
        history.print()

    def opt_redo(self, args: typing.List[str]):
        """
        redo <history-number>
            Redo some history request(s) given by <history-number> (GET requests are not redone)
        """
        try:
            history.redo(int(args[0]))
        except ValueError as e:
            cli_warning("invalid input: {}".format(e))

    def opt_undo(self, args: typing.List[str]):
        """
        undo <history-number>
            Undo some history request(s) given by <history-number> (GET requests cannot be undone)
        """
        try:
            history.undo(int(args[0]))
        except ValueError as e:
            cli_warning("invalid input: {}".format(e))


class Host(CommandBase):
    """
    Create, delete or edit host.
        host <option> <argument(s)>
    """

    def opt_info(self, args: typing.List[str]) -> None:
        """
        info <name|ip>
            Print information about host. If <name> is an alias the cname hosts info is shown.
        """
        if len(args) < 1:
            name_or_ip = input("Enter name or ip> ")
        else:
            name_or_ip = args[0]

        # Get host info or raise exception
        info = host_info_by_name_or_ip(name_or_ip)

        # Pretty print all host info
        print_host_name(info["name"])
        print_contact(info["contact"])
        if info["comment"]:
            print_comment(info["comment"])
        print_ipaddresses(info["ipaddress"])
        print_ttl(info["ttl"])
        if info["hinfo"]:
            print_hinfo(hinfo_id_to_strings(info["hinfo"]))
        if info["loc"]:
            print_loc(info["loc"])
        for cname in aliases_of_host(info["name"]):
            print_cname(cname, info["name"])
        for txt in info["txt"]:
            print_txt(txt["txt"])
        cli_info("printed host info for {}".format(info["name"]))

    def opt_remove(self, args: typing.List[str]) -> None:
        """
        remove <name|ip>
            Remove host. If <name> is an alias the cname host is removed.
        """
        if len(args) < 1:
            name_or_ip = input("Enter name or ip> ")
        else:
            name_or_ip = args[0]

        info = host_info_by_name_or_ip(name_or_ip)

        if len(info["ipaddress"]) > 1 and "y" not in args:
            cli_warning("{} has multiple ipaddresses, must force")

        # Require force if host has any aliases
        aliases = aliases_of_host(info["name"])
        if len(aliases):
            if "y" not in args:
                cli_warning("{} has {} aliases, must force".format(info["name"], len(aliases)))
            else:
                for alias in aliases:
                    url = "http://{}:{}/hosts/{}".format(
                        conf["server_ip"],
                        conf["server_port"],
                        alias,
                    )
                    # Cannot undo delete because of hosts CNAME record
                    history.record_delete(url, old_data=dict(), undoable=False)
                    delete(url)
                    cli_info("deleted alias host {} when removing {}".format(alias, info["name"]))

        # TODO FORCE: kreve force hvis host har:  SRV eller NAPTR pekende på seg

        # To be able to undo the delete the ipaddress field of the 'old_data' has to be an ipaddress
        # string
        if len(info["ipaddress"]) > 0:
            info["ipaddress"] = info["ipaddress"][0]["ipaddress"]

        # Delete host
        url = "http://{}:{}/hosts/{}".format(conf["server_ip"], conf["server_port"], info["name"])
        history.record_delete(url, old_data=info)
        delete(url)
        cli_info("removed {}".format(info["name"]), print_msg=True)

    def opt_add(self, args: typing.List[str]) -> None:
        """
        add <name> <ip/net> <contact> [-hinfo <hinfo>] [-comment <comment>]
            Add a new host with the given name, ip or subnet and contact. hinfo and comment
            are optional.
        """
        hi_list = hinfo_list()
        if len(args) < 3:
            name = input("Enter host name> ") if len(args) < 1 else args[0]
            ip_or_net = input("Enter subnet or ip> ") if len(args) < 2 else args[1]
            contact = input("Enter contact> ")
            hinfo = input("Enter hinfo (optional)> ")
            while hinfo == "?":
                print_hinfo_list(hi_list)
                hinfo = input("Enter hinfo (optional)> ")
            comment = input("Enter comment (optional)> ")
        else:
            name = args[0]
            ip_or_net = args[1]
            contact = args[2]
            hinfo = "" if "-hinfo" not in args else args[args.index("-hinfo") + 1]
            comment = "" if "-comment" not in args else args[args.index("-comment") + 1]

        # Verify hinfo id
        if hinfo:
            hinfo = int(hinfo)
            if not 0 < hinfo <= len(hi_list):
                cli_warning("invalid hinfo ({}) when trying to add {}".format(hinfo, name))

        # Handle arbitrary ip from subnet if received a subnet
        if re.match(r"^.*([.:]0|::)/$", ip_or_net) or is_valid_subnet(ip_or_net):
            # TODO SUBNET: handle random ip address
            ip = choose_ip_from_subnet(ip_or_net)
        else:
            ip = ip_or_net

        # Contact sanity check
        if not is_valid_email(contact):
            cli_warning("invalid mail address ({}) when trying to add {}".format(contact, name))

        # Check if given host exists on either short or long form
        try:
            name = resolve_input_name(name)
        except HostNotFoundWarning:
            pass
        else:
            if "y" not in args:
                cli_warning("host {} already exists, must force".format(name))
            else:
                url = "http://{}:{}/hosts/{}".format(
                    conf["server_ip"],
                    conf["server_port"],
                    name,
                )
                history.record_delete(url, dict(), undoable=False)
                delete(url)
                cli_info("deleted existing host {}".format(name))

        # Always use long form host name
        name = name if is_longform(name) else to_longform(name)

        # Create the new host with an ip address
        url = "http://{}:{}/hosts/".format(conf["server_ip"], conf["server_port"])
        data = {
            "name": name,
            "ipaddress": ip,
            "contact": contact,
            "hinfo": hinfo or None,
            "comment": comment or None,
        }
        history.record_post(url, resource_name=name, new_data=data)
        post(url, **data)
        cli_info("created host {}".format(name), print_msg=True)

    def opt_set_contact(self, args: typing.List[str]) -> None:
        """
        set_contact <name> <contact>
            Set contact for host. If <name> is an alias the cname host is updated.
        """
        if len(args) < 2:
            name = input("Enter host name> ") if len(args) < 1 else args[0]
            contact = input("Enter contact> ")
        else:
            name = args[0]
            contact = args[1]

        # Contact sanity check
        if not is_valid_email(contact):
            cli_warning("invalid mail address {} (target host: {})".format(contact, name))

        # Get host info for <name> or its cname
        info = host_info_by_name(name)
        old_data = {"contact": info["contact"]}
        new_data = {"contact": contact}

        # Update contact information
        url = "http://{}:{}/hosts/{}".format(conf["server_ip"], conf["server_port"], info["name"])
        history.record_patch(url, new_data, old_data)
        patch(url, contact=contact)
        cli_info("Updated contact of {} to {}".format(info["name"], contact), print_msg=True)

    def opt_set_comment(self, args: typing.List[str]) -> None:
        """
        set_comment <name> <comment>
            Set comment for host. If <name> is an alias the cname host is updated.
        """
        if len(args) < 2:
            name = input("Enter host name> ") if len(args) < 1 else args[0]
            comment = input("Enter comment> ")
        else:
            name = args[0]
            comment = " ".join(args[1:])

        # Get host info for <name> or its cname
        info = host_info_by_name(name)
        old_data = {"comment": info["comment"] or ""}
        new_data = {"comment": comment}

        # Update comment
        url = "http://{}:{}/hosts/{}".format(conf["server_ip"], conf["server_port"], info["name"])
        history.record_patch(url, new_data, old_data)
        patch(url, comment=comment)
        cli_info("updated comment of {} to \"{}\"".format(info["name"], comment), print_msg=True)

    def opt_rename(self, args: typing.List[str]) -> None:
        """
        rename <old-name> <new-name>
            Rename host. If <old-name> is an alias then the alias is renamed.
        """
        if len(args) < 2:
            old_name = input("Enter old name> ") if len(args) < 1 else args[0]
            new_name = input("Enter new name> ")
        else:
            old_name = args[0]
            new_name = args[1]

        old_name = resolve_input_name(old_name)

        # Require force if the new name is already in use
        try:
            info = host_info_by_name(new_name, follow_cnames=False)
        except HostNotFoundWarning:
            pass
        else:
            if "y" not in args:
                # QUESTION: should inform if the existing host has any records (like remove)?
                cli_warning("host {} already exists, must force".format(info["name"]))
            for alias in aliases_of_host(info["name"]):
                url = "http://{}:{}/hosts/{}".format(
                    conf["server_ip"],
                    conf["server_port"],
                    alias,
                )
                history.record_delete(url, dict(), undoable=False)
                delete(url)
                cli_info("deleted alias host {} when removing {} before renaming {}".format(
                    alias,
                    info["name"],
                    old_name,
                ))
            # TODO FORCE: check and remove SRV, NAPTR pointing at existing host
            url = "http://{}:{}/hosts/{}".format(
                conf["server_ip"],
                conf["server_port"],
                info["name"],
            )
            history.record_delete(url, dict(), undoable=False)
            delete(url)
            cli_info("deleted existing host {}".format(new_name))

        # Always use long form host name
        new_name = new_name if is_longform(new_name) else to_longform(new_name)
        old_data = {"name": old_name}
        new_data = {"name": new_name}

        # Rename host
        url = "http://{}:{}/hosts/{}".format(conf["server_ip"], conf["server_port"], old_name)
        # Cannot redo/undo now since it changes name
        history.record_patch(url, new_data, old_data, redoable=False, undoable=False)
        patch(url, name=new_name)
        cli_info("renamed {} to {}".format(old_name, new_name), print_msg=True)

        url = "http://{}:{}/cnames/?cname={}".format(
            conf["server_ip"],
            conf["server_port"],
            old_name,
        )
        history.record_get(url)
        cnames = get(url).json()
        for cname in cnames:
            url = "http://{}:{}/cnames/{}".format(
                conf["server_ip"],
                conf["server_port"],
                cname["id"],
            )
            old_data = {"cname": old_name}
            new_data = {"cname": new_name}
            history.record_patch(url, new_data, old_data)
            patch(url, cname=new_name)

        # TODO SRV: Update all SRV pointing at host when renaming it
        # TODO NAPTR: Update all NAPTR pointing at host when renaming it

    def opt_a_add(self, args: typing.List[str]) -> None:
        """
        a_add <name> <ip|subnet>
            Add an A record to host. If <name> is an alias the cname host is used.
        """
        if len(args) < 2:
            name = input("Enter host name> ") if len(args) < 1 else args[0]
            ip_or_subnet = input("Enter ip/subnet> ")
        else:
            name = args[0]
            ip_or_subnet = args[1]

        # Get host info for <name> or its cname
        info = host_info_by_name(name)

        # Verify ip or get ip from subnet
        if is_valid_ipv4(ip_or_subnet):
            ip = ip_or_subnet
        elif is_valid_subnet(ip_or_subnet):
            # TODO SUBNET: choose random ip (?)
            cli_warning("subnets not implemented")
            ip = choose_ip_from_subnet(ip_or_subnet)
        else:
            cli_warning("invalid ipv4 nor subnet: \"{}\" (target host: {})".format(
                ip_or_subnet,
                info["name"])
            )

        data = {
            "hostid": info["hostid"],
            "ipaddress": ip,
        }

        # Add A record
        url = "http://{}:{}/ipaddresses/".format(conf["server_ip"], conf["server_port"])
        history.record_post(url, ip, data)
        post(url, **data)
        cli_info("added ip {} to {}".format(ip, info["name"]), print_msg=True)

    def opt_a_remove(self, args: typing.List[str]) -> None:
        """
        a_remove <name> <ip>
            Remove A record from host. If <name> is an alias the cname host is used.
        """
        if len(args) < 2:
            name = input("Enter host name> ") if len(args) < 1 else args[0]
            ip = input("Enter ip> ")
        else:
            name = args[0]
            ip = args[1]

        # Ip sanity check
        if not is_valid_ipv4(ip):
            cli_warning("not a valid ipv4: \"{}\"".format(ip))

        # Check that ip belongs to host
        info = host_info_by_name(name)
        found = False
        for rec in info["ipaddress"]:
            if rec["ipaddress"] == ip:
                found = True
                break
        if not found:
            cli_warning("{} is not owned by {}".format(ip, info["name"]))

        old_data = {
            "hostid": info["hostid"],
            "ipaddress": ip,
        }

        # Remove ip
        url = "http://{}:{}/ipaddresses/{}".format(conf["server_ip"], conf["server_port"], ip)
        history.record_delete(url, old_data)
        delete(url)
        cli_info("removed ip {} from {}".format(ip, info["name"]), print_msg=True)

    def opt_a_change(self, args: typing.List[str]) -> None:
        """
        a_change <name> <old-ip> <new-ip-or-subnet>
            Change A record. If <name> is an alias the cname host is used.
        """
        if len(args) < 3:
            name = input("Enter host name> ") if len(args) < 1 else args[0]
            old_ip = input("Enter old ip> ") if len(args) < 2 else args[1]
            ip_or_subnet = input("Enter new ip/subnet> ")
        else:
            name = args[0]
            old_ip = args[1]
            ip_or_subnet = args[2]

        # Ip and subnet sanity checks
        if not is_valid_ipv4(old_ip):
            cli_warning("invalid ipv4 \"{}\" (target host {})".format(old_ip, name))
        elif not is_valid_ipv4(ip_or_subnet) and not is_valid_subnet(ip_or_subnet):
            cli_warning(
                "invalid ipv4 nor subnet \"{}\" (target host {})".format(ip_or_subnet, name))

        # Check that ip belongs to host
        info = host_info_by_name(name)
        found = False
        for rec in info["ipaddress"]:
            if rec["ipaddress"] == old_ip:
                found = True
                break
        if not found:
            cli_warning("{} is not owned by {}".format(old_ip, info["name"]))

        # Handle arbitrary ip from subnet if received a subnet
        if is_valid_ipv4(ip_or_subnet):
            ip = ip_or_subnet
        else:
            # TODO SUBNET: choose random ip from subnet
            cli_warning("subnets not implemented")
            ip = choose_ip_from_subnet(ip_or_subnet)

        old_data = {"ipaddress": old_ip}
        new_data = {"ipaddress": ip}

        # Update A record ip address
        url = "http://{}:{}/ipaddresses/{}".format(conf["server_ip"], conf["server_port"], old_ip)
        # Cannot redo/undo since resource name changes
        history.record_patch(url, new_data, old_data, redoable=False, undoable=False)
        patch(url, ipaddress=ip)
        cli_info("updated ip {} to {} for {}".format(old_ip, ip, info["name"]), print_msg=True)

    def opt_a_show(self, args: typing.List[str]) -> None:
        """
        a_show <name>
            Show hosts ipaddresses. If <name> is an alias the cname host is used.
        """
        name = input("Enter host name> ") if len(args) < 1 else args[0]
        info = host_info_by_name(name)
        print_ipaddresses(info["ipaddress"])
        cli_info("showed ip addresses for {}".format(info["name"]))

    def opt_aaaa_add(self, args: typing.List[str]) -> None:
        """
        aaaa_add <name> <ipv6>
            Add an AAAA record to host. If <name> is an alias the cname host is used.
        """
        if len(args) < 2:
            name = input("Enter host name> ") if len(args) < 1 else args[0]
            ip = input("Enter ipv6> ")
        else:
            name = args[0]
            ip = args[1]

        # Verify host and get host id
        info = host_info_by_name(name)

        # Verify ip or get ip from subnet
        if not is_valid_ipv6(ip):
            cli_warning("not a valid ipv6 \"{}\" (target host {})".format(ip, info["name"]))

        data = {
            "hostid": info["hostid"],
            "ipaddress": ip,
        }

        # Create AAAA records
        url = "http://{}:{}/ipaddresses/".format(conf["server_ip"], conf["server_port"])
        history.record_post(url, ip, data)
        post(url, **data)
        cli_info("added ip {} to {}".format(ip, info["name"]), print_msg=True)

    def opt_aaaa_remove(self, args: typing.List[str]) -> None:
        """
        aaaa_remove <name> <ipv6>
            Remove AAAA record from host. If <name> is an alias the cname host is used.
        """
        if len(args) < 2:
            name = input("Enter host name> ") if len(args) < 1 else args[0]
            ip = input("Enter ipv6> ")
        else:
            name = args[0]
            ip = args[1]

        info = host_info_by_name(name)

        # Ipv6 sanity check
        if not is_valid_ipv6(ip):
            cli_warning("not a valid ipv6 \"{}\" (target host {})".format(ip, info["name"]))

        # Check that ip belongs to host
        found = False
        for rec in info["ipaddress"]:
            if rec["ipaddress"] == ip:
                found = True
                break
        if not found:
            cli_warning("{} is not owned by {}".format(ip, info["name"]))

        old_data = {
            "hostid": info["hostid"],
            "ipaddress": ip,
        }

        # Delete AAAA record
        url = "http://{}:{}/ipaddresses/{}".format(conf["server_ip"], conf["server_port"], ip)
        history.record_delete(url, old_data)
        delete(url)
        cli_info("removed {} from {}".format(ip, info["name"]), print_msg=True)

    def opt_aaaa_change(self, args: typing.List[str]) -> None:
        """
        aaaa_change <name> <old-ipv6> <new-ipv6>
            Change AAAA record. If <name> is an alias the cname host is used.
        """
        if len(args) < 3:
            name = input("Enter host name> ") if len(args) < 1 else args[0]
            old_ip = input("Enter old ipv6> ") if len(args) < 2 else args[1]
            new_ip = input("Enter new ipv6> ")
        else:
            name = args[0]
            old_ip = args[1]
            new_ip = args[2]

        info = host_info_by_name(name)

        # Ipv6 sanity checks
        if not is_valid_ipv6(old_ip):
            cli_warning("not a valid ipv6 \"{}\" (target host {})".format(old_ip, info["name"]))
        elif not is_valid_ipv6(new_ip):
            cli_warning("not a valid ipv6 \"{}\" (target host {})".format(new_ip, info["name"]))

        # Check that ip belongs to host
        found = False
        for rec in info["ipaddress"]:
            if rec["ipaddress"] == old_ip:
                found = True
                break
        if not found:
            cli_warning("\"{}\" is not owned by {}".format(old_ip, info["name"]))

        old_data = {"ipaddress": old_ip}
        new_data = {"ipaddress": new_ip}

        # Update AAAA records ip address
        url = "http://{}:{}/ipaddresses/{}".format(conf["server_ip"], conf["server_port"], old_ip)
        # Cannot redo/undo since recourse name changes
        history.record_patch(url, new_data, old_data, redoable=False, undoable=False)
        patch(url, ipaddress=new_ip)
        cli_info("changed ip {} to {} for {}".format(old_ip, new_ip, info["name"]), print_msg=True)

    def opt_aaaa_show(self, args: typing.List[str]) -> None:
        """
        aaaa_show <name>
            Show hosts ipaddresses. If <name> is an alias the cname host is used.
        """
        name = input("Enter host name> ") if len(args) < 1 else args[0]
        info = host_info_by_name(name)
        print_ipaddresses(info["ipaddress"])
        cli_info("showed aaaa records for {}".format(info["name"]))

    def opt_ttl_set(self, args: typing.List[str]) -> None:
        """
        ttl_set <name> <ttl>
            Set ttl for host. Valid values are 300 <= TTL <= 68400 or "default". If <name> is an
            alias the alias host is updated.
        """
        if len(args) < 2:
            name = input("Enter host name> ") if len(args) < 1 else args[0]
            ttl = input("Enter ttl> ")
        else:
            name = args[0]
            ttl = args[1]

        info = host_info_by_name(name)

        # TTL sanity check
        if not is_valid_ttl(ttl):
            cli_warning("invalid TTL value: {} (target host {})".format(ttl, info["name"]))

        old_data = {"ttl": info["ttl"] or -1}
        new_data = {"ttl": ttl if ttl != "default" else -1}

        # Update TTL
        url = "http://{}:{}/hosts/{}".format(conf["server_ip"], conf["server_port"], info["name"])
        history.record_patch(url, new_data, old_data)
        patch(url, **new_data)
        cli_info("updated TTL for {}".format(info["name"]), print_msg=True)

    def opt_ttl_remove(self, args: typing.List[str]) -> None:
        """
        ttl_remove <name>
            Remove explicit TTL for host. If <name> is an alias the alias host is updated.
        """
        name = input("Enter host name> ") if len(args) < 1 else args[0]
        info = host_info_by_name(name)
        old_data = {"ttl": info["ttl"]}
        new_data = {"ttl": -1}

        # Remove TTL value
        url = "http://{}:{}/hosts/{}".format(conf["server_ip"], conf["server_port"], info["name"])
        history.record_patch(url, new_data, old_data)
        patch(url, ttl=-1)
        cli_info("removed TTL for {}".format(info["name"]), print_msg=True)

    def opt_ttl_show(self, args: typing.List[str]) -> None:
        """
        ttl_show <name>
            Show ttl for host. If <name> is an alias the alias hosts TTL is shown.
        """
        name = input("Enter host name> ") if len(args) < 1 else args[0]
        info = host_info_by_name(name)
        print_ttl(info["ttl"])
        cli_info("showed TTL for {}".format(info["name"]))

    def opt_cname_add(self, args: typing.List[str]) -> None:
        """
        cname_add <existing-name> <new-alias>
            Add a CNAME record to host. If <existing-name> is an alias the cname host is used as
            target for <new-alias>.
        """
        if len(args) < 2:
            name = input("Enter name> ") if len(args) < 1 else args[0]
            alias = input("Enter alias> ")
        else:
            name = args[0]
            alias = args[1]

        host_info = host_info_by_name(name)

        # If alias name already exists the host cannot have any records
        try:
            alias_info = host_info_by_name(alias)
        except HostNotFoundWarning:
            alias_info = None
        else:
            if alias_info["hinfo"] or \
                    alias_info["loc"] or \
                    alias_info["cname"] or \
                    alias_info["ipaddress"] or \
                    alias_info["txt"]:
                cli_warning("host {} already exists and has record(s)".format(alias_info["name"]))

        # Create cname host if it doesn't exist
        if not alias_info:
            alias = alias if is_longform(alias) else to_longform(alias)
            data = {
                "name": alias,
                "contact": host_info["contact"],
            }
            url = "http://{}:{}/hosts/".format(conf["server_ip"], conf["server_port"])
            history.record_post(url, alias, data)
            post(url, **data)
            alias_info = host_info_by_name(alias)

        # Create CNAME record
        url = "http://{}:{}/cnames/".format(conf["server_ip"], conf["server_port"])
        history.record_post(url, "", dict(), redoable=False, undoable=False)
        post(url, hostid=alias_info["hostid"], cname=host_info["name"])
        cli_info("Added cname alias {} for {}".format(alias_info["name"], host_info["name"]),
                 print_msg=True)

    def opt_cname_remove(self, args: typing.List[str]) -> None:
        """
        cname_remove <name> <alias-to-delete>
            Remove CNAME record.
        """
        if len(args) < 2:
            name = input("Enter name> ") if len(args) < 1 else args[0]
            alias = input("Enter alias> ")
        else:
            name = args[0]
            alias = args[1]

        host_name = resolve_input_name(name)
        alias_info = host_info_by_name(alias, follow_cnames=False)

        # Check that cname host is an alias for host
        cnames = alias_info["cname"]
        if len(cnames) < 1:
            cli_warning("\"{}\" doesn't have any CNAME records.".format(alias_info["name"]))
        if cnames[0]["cname"] != host_name:
            cli_warning("\"{}\" is not an alias for \"{}\"".format(alias_info["name"], host_name))

        # Delete CNAME host
        url = "http://{}:{}/hosts/{}".format(conf["server_ip"], conf["server_port"],
                                             alias_info["name"])
        history.record_delete(url, dict(), undoable=False)
        delete(url)
        cli_info("Removed cname alias {} for {}".format(alias_info["name"], host_name),
                 print_msg=True)

    def opt_cname_show(self, args: typing.List[str]) -> None:
        """
        cname_show <name>
            Show CNAME records for host. If <name> is an alias the cname hosts aliases are shown.
        """
        name = input("Enter name> ") if len(args) < 1 else args[0]

        # Gets the host info of the named host or the cname host if name is an alias
        info = host_info_by_name(name)

        for alias in aliases_of_host(info["name"]):
            print_cname(alias, info["name"])
        cli_info("showed cname aliases for {}".format(info["name"]))

    def opt_loc_set(self, args: typing.List[str]) -> None:
        """
        loc_set <name> <loc>
            Set location of host. If <name> is an alias the cname host is updated.
        """
        if len(args) < 2:
            name = input("Enter host name> ") if len(args) < 1 else args[0]
            loc = input("Enter loc> ")
        else:
            name = args[0]
            loc = " ".join(args[1:])

        info = host_info_by_name(name)

        # LOC sanity check
        if not is_valid_loc(loc):
            cli_warning("invalid LOC \"{}\" (target host {})".format(loc, info["name"]))

        old_data = {"loc": info["loc"] or ""}
        new_data = {"loc": loc}

        # Update LOC
        url = "http://{}:{}/hosts/{}".format(conf["server_ip"], conf["server_port"], info["name"])
        history.record_patch(url, new_data, old_data)
        patch(url, loc=loc)
        cli_info("updated LOC to {} for {}".format(loc, info["name"]), print_msg=True)

    def opt_loc_remove(self, args: typing.List[str]) -> None:
        """
        loc_remove <name>
            Remove location from host. If <name> is an alias the cname host is updated.
        """
        name = input("Enter host name> ") if len(args) < 1 else args[0]
        info = host_info_by_name(name)
        old_data = {"loc": info["loc"]}
        new_data = {"loc": ""}
        url = "http://{}:{}/hosts/{}".format(conf["server_ip"], conf["server_port"], info["name"])
        history.record_patch(url, new_data, old_data)
        patch(url, loc="")
        cli_info("removed LOC for {}".format(info["name"]), print_msg=True)

    def opt_loc_show(self, args: typing.List[str]) -> None:
        """
        loc_show <name>
            Show location of host. If <name> is an alias the cname hosts LOC is shown.
        """
        name = input("Enter name> ") if len(args) < 1 else args[0]
        info = host_info_by_name(name)
        print_loc(info["loc"])
        cli_info("showed LOC for {}".format(info["name"]))

    def opt_hinfo_set(self, args: typing.List[str]) -> None:
        """
        hinfo_set <name> <hinfo>
            Set hinfo for host. If <name> is an alias the cname host is updated.
        """
        hi_list = hinfo_list()
        if len(args) < 2:
            name = input("Enter host name> ") if len(args) < 1 else args[0]
            hinfo = input("Enter hinfo> ")
            while hinfo == "?":
                print_hinfo_list(hi_list)
                hinfo = input("Enter hinfo> ")
        else:
            name = args[0]
            hinfo = args[1]

        # Hinfo sanity check
        hinfo = int(hinfo)
        if not 0 < hinfo <= len(hi_list):
            cli_warning("invalid hinfo.")

        info = host_info_by_name(name)
        old_data = {"hinfo": info["hinfo"] or -1}
        new_data = {"hinfo": hinfo}

        # Update hinfo
        url = "http://{}:{}/hosts/{}".format(conf["server_ip"], conf["server_port"], info["name"])
        history.record_patch(url, new_data, old_data)
        patch(url, hinfo=hinfo)
        cli_info("updated hinfo to {} for {}".format(hinfo, info["name"]), print_msg=True)

    def opt_hinfo_remove(self, args: typing.List[str]) -> None:
        """
        hinfo_remove <name>
            Remove hinfo for host. If <name> is an alias the cname host is updated.
        """
        name = input("Enter host name> ") if len(args) < 1 else args[0]
        info = host_info_by_name(name)
        old_data = {"hinfo": info["hinfo"]}
        new_data = {"hinfo": -1}
        url = "http://{}:{}/hosts/{}".format(conf["server_ip"], conf["server_port"], info["name"])
        history.record_patch(url, new_data, old_data)
        patch(url, hinfo=-1)
        cli_info("removed hinfo for {}".format(info["name"]), print_msg=True)

    def opt_hinfo_show(self, args: typing.List[str]) -> None:
        """
        hinfo_show <name>
            Show hinfo for host. If <name> is an alias the cname hosts hinfo is shown.
        """
        name = input("Enter host name> ") if len(args) < 1 else args[0]
        info = host_info_by_name(name)
        print_hinfo(hinfo_id_to_strings(info["hinfo"]))
        cli_info("showed hinfo for {}".format(info["name"]))

    def opt_srv_add(self, args: typing.List[str]) -> None:
        """
        srv_add <service-name> <pri> <weight> <port> <target-name>
            Add SRV record.
        """
        if len(args) < 5:
            sname = input("Enter service name> ") if len(args) < 1 else args[0]
            pri = input("Enter priority> ") if len(args) < 2 else args[1]
            weight = input("Enter weight> ") if len(args) < 3 else args[2]
            port = input("Enter port> ") if len(args) < 4 else args[3]
            name = input("Enter target name> ")
        else:
            sname = args[0]
            pri = args[1]
            weight = args[2]
            port = args[3]
            name = args[4]

        try:
            host_name = resolve_input_name(name)
        except HostNotFoundWarning:
            if "y" not in args:
                cli_warning("{} doesn't exist. Must force")
            host_name = name

        sname = sname if is_longform(sname) else to_longform(sname, trailing_dot=True)
        url = "http://{}:{}/srvs/?service={}".format(conf["server_ip"], conf["server_port"], sname)
        history.record_get(url)
        srvs = get(url).json()
        if len(srvs) > 0:
            entry_exists = True
        else:
            entry_exists = False

        data = {
            "service": sname,
            "priority": pri,
            "weight": weight,
            "port": port,
            "target": host_name,
        }

        url = "http://{}:{}/srvs/".format(conf["server_ip"], conf["server_port"])
        history.record_post(url, "", data, undoable=False)
        post(url, **data)
        if entry_exists:
            cli_info("Added SRV record {} with target {} to existing entry."
                     .format(sname, host_name), print_msg=True)
        else:
            cli_info("Added SRV record {} with target {}".format(sname, host_name), print_msg=True)

    def opt_srv_remove(self, args: typing.List[str]) -> None:
        """
        srv_remove <service-name>
            Remove SRV record.
        """
        sname = input("Enter service name> ") if len(args) < 1 else args[0]
        sname = sname if is_longform(sname) else to_longform(sname, trailing_dot=True)
        url = "http://{}:{}/srvs/?service={}".format(conf["server_ip"], conf["server_port"], sname)
        history.record_get(url)
        srvs = get(url).json()
        if len(srvs) == 0:
            cli_warning("not service named {}".format(sname))
        elif len(srvs) > 1 and "y" not in args:
            cli_warning("multiple services named {}, must force".format(sname))
        for srv in srvs:
            assert isinstance(srv, dict)
            url = "http://{}:{}/srvs/{}".format(
                conf["server_ip"],
                conf["server_port"],
                srv["srvid"],
            )
            history.record_delete(url, srv, redoable=False)
            delete(url)
            cli_info("removed SRV record {} with target {}".format(srv["service"], srv["target"]),
                     print_msg=True)

    def opt_srv_show(self, args: typing.List[str]) -> None:
        """
        srv_show <service-name>
            Show SRV show.
        """
        sname = input("Enter service name> ") if len(args) < 1 else args[0]
        url = "http://{}:{}/srvs/?service__contains={}".format(
            conf["server_ip"],
            conf["server_port"],
            sname,
        )
        history.record_get(url)
        srvs = get(url).json()
        if len(srvs) < 1:
            cli_warning("no service matching {}".format(sname))
        padding = 0
        for srv in srvs:
            if len(srv["service"]) > padding:
                padding = len(srv["service"])
        prev_name = ""
        for srv in sorted(srvs, key=lambda k: k["service"]):
            if prev_name == srv["service"]:
                srv["service"] = ""
            else:
                prev_name = srv["service"]
            print_srv(srv, padding)
        cli_info("showed entries for SRV {}".format(sname))

    def opt_txt_add(self, args: typing.List[str]) -> None:
        """
        txt_add <name> <text>
            Add a txt record to host. <text> must be enclosed in double quotes if it contains more
            than one word.
        """
        pass

    def opt_txt_remove(self, args: typing.List[str]) -> None:
        """
        txt_remove <name>
        """
        pass

    def opt_used_list(self, args: typing.List[str]) -> None:
        """
        used_list <ip>
            List addresses used on the subnet which <ip> belongs to.
        """
        # TODO: implementer used_list
        pass


class Zone(CommandBase):
    """
    Handle zones.
        zone <option> <argument(s)>
    """

    def opt_create(self, args: typing.List[str]):
        """
        create <zone-name> (<primary-ns> <contact> <serialno> <refresh> <retry> <expire> <ttl>)
            Create new zone.
        """
        pass

    def opt_delete(self, args: typing.List[str]):
        pass

    def opt_set_ns(self, args: typing.List[str]):
        pass

    def opt_set_soa(self, args: typing.List[str]):
        pass


class Subnet(CommandBase):
    """
    Handle subnets.
        subnet <option> <argument(s)>
    """

    def opt_info(self, args: typing.List[str]):
        """
        info <subnet>
            Display subnet info
        """
        pass

    def opt_set_vlan(self, args: typing.List[str]):
        """
        set_vlan <subnet> <vlan>
            Set VLAN for subnet
        """
        pass

    def opt_set_description(self, args: typing.List[str]):
        """
        set_description <subnet> <description>
            Set description for subnet
        """
        pass

    def opt_set_name_prefix(self, args: typing.List[str]):
        """
        set_name_prefix <subnet> <name-prefix>
            Set name prefix for subnet
        """
        pass

    def opt_set_dns_delegated(self, args: typing.List[str]):
        """
        set_dns_delegated <subnet>
            Set that DNS-administration is being handled elsewhere.
        """
        pass

    def opt_unset_dns_delegated(self, args: typing.List[str]):
        """
        unset_dns_delegated <subnet>
            Set that DNS-administration is not being handled elsewhere.
        """
        pass

    def opt_set_reserved(self, args: typing.List[str]):
        """
        set_reserved <subnet> <number>
            Set number of reserved addresses.
        """
        pass

    def opt_import(self, args: typing.List[str]):
        """
        import <file>
            Import subnet data from <file>.
        """
        pass
