import ipaddress
import operator
from urllib.parse import urlencode

from .cli import Flag, cli
from .history import history
from .log import cli_info, cli_warning
from .util import delete, get_list, is_valid_network, post

###################################
#  Add the main command 'access'  #
###################################

permission = cli.add_command(
    prog='permission',
    description='Manage permissions.',
)


##########################################
# Implementation of sub command 'list' #
##########################################

def network_list(args):
    """
    Lists permissions for networks
    """

    def _networksort(data):
        # Since we can not get mreg to sort by networks, do it here
        v4 = []
        v6 = []
        for i in data:
            net = ipaddress.ip_network(i['range'])
            i['range'] = net
            if net.version == 4:
                v4.append(i)
            else:
                v6.append(i)
        v4.sort(key=operator.itemgetter('range'))
        v6.sort(key=operator.itemgetter('range'))
        v4.extend(v6)
        return v4

    query = {}
    params = ""
    if args.group is not None:
        query['group'] = args.group
    if query:
        params = "&{}".format(urlencode(query))
    permissions = get_list("/permissions/netgroupregex/?ordering=group{}".format(params))

    data = []
    if args.range is not None:
        argnetwork = ipaddress.ip_network(args.range)
        for i in permissions:
            permnet = ipaddress.ip_network(i['range'])
            if argnetwork.version == permnet.version and \
               argnetwork.supernet_of(ipaddress.ip_network(i['range'])):
                data.append(i)
    else:
        data = permissions

    if not data:
        cli_info("No permissions found", True)
        return

    def print_perm(range, group, regex):
        print("{0:<{1}}{2:<{3}} {4}".format(range, 20, group, 16, regex))

    print_perm("Range", "Group", "Regex")
    for i in _networksort(data):
        print_perm(str(i['range']), i['group'], i['regex'])


permission.add_command(
    prog='network_list',
    description='List permissions for networks',
    short_desc='List permissions for networks',
    callback=network_list,
    flags=[
        Flag('-group',
             description='Group with access',
             metavar='GROUP'),
        Flag('-range',
             description='Network range',
             metavar='RANGE'),
    ]
)

##########################################
# Implementation of sub command 'add' #
##########################################


def network_add(args):
    """
    Add permission for network
    """

    if not is_valid_network(args.range):
        cli_warning(f'Invalid range: {args.range}')

    data = {
        'range': args.range,
        'group': args.group,
        'regex': args.regex,
    }
    path = "/permissions/netgroupregex/"
    history.record_get(path, "", data)
    post(path, **data)
    cli_info(f"Added permission to {args.range}", True)


permission.add_command(
    prog='network_add',
    description='Add permission for network',
    short_desc='Add permission for network',
    callback=network_add,
    flags=[
        Flag('range',
             description='Network range',
             metavar='RANGE'),
        Flag('group',
             description='Group with access',
             metavar='GROUP'),
        Flag('regex',
             description='Regular expression',
             metavar='REGEX'),
    ]
)


##########################################
# Implementation of sub command 'remove' #
##########################################

def network_remove(args):
    """
    Remove permission for networks
    """

    query = {
        'group': args.group,
        'range': args.range,
        'regex': args.regex,
    }
    params = "{}".format(urlencode(query))
    permissions = get_list("/permissions/netgroupregex/?{}".format(params))

    if not permissions:
        cli_warning("No matching permission found", True)
        return

    assert len(permissions) == 1, "Should only match one permission"
    id = permissions[0]['id']
    path = f"/permissions/netgroupregex/{id}"
    history.record_delete(path, dict(), undoable=False)
    delete(path)
    cli_info(f"Removed permission for {args.range}", True)


permission.add_command(
    prog='network_remove',
    description='Remove permission for network',
    short_desc='Remove permission for network',
    callback=network_remove,
    flags=[
        Flag('range',
             description='Network range',
             metavar='RANGE'),
        Flag('group',
             description='Group with access',
             metavar='GROUP'),
        Flag('regex',
             description='Regular expression',
             metavar='REGEX'),
    ]
)
