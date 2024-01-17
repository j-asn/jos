#
# Copyright (c) 2024 Jonathan Nilsen
#
# SPDX-License-Identifier: Apache-2.0
#
from __future__ import annotations

from pathlib import Path
from typing import List, Optional, Sequence, NamedTuple, Union, Mapping, Any
import argparse
import io
import logging as log
import os
import re
import sys

from click_option_group import optgroup, RequiredMutuallyExclusiveOptionGroup
import click
import pylink
import svd

SESSION_CONFIG_ENV_VAR = "_JOS_SESSION_CONFIG"


def parse_args() -> argparse.Namespace:
    p = argparse.ArgumentParser(
        prog="jos",
        description="Read and write embedded device memory using JLink and SVD files",
        allow_abbrev=False,
    )

    p.add_argument(
        "-c",
        "--config",
        type=click.Path(path_type=Path),
        default=os.environ.get(SESSION_CONFIG_ENV_VAR),
        help="TOML configuration file",
    )

    sub = p.add_subparsers(dest="command")

    p_list = sub.add_parser("list", description="List elements in the SVD file.")
    add_svd_options(p_list)

    p_session = sub.add_parser("session", description="Set session configuration.")
    # p_session = 

    return p.parse_args()


def add_svd_options(parser: argparse.ArgumentParser) -> None:
    parser.add_argument("-s", "--svd-file", type=Path, required=True, help="Path to the SVD file.") 



@click.command("jos")
@optgroup.group("General options")
@optgroup.option(
    "--show", is_flag=True, help="Show available elements in the SVD file."
)
@optgroup.option(
    "-c",
    "--config",
    type=click.Path(path_type=Path),
    default=os.environ.get(SESSION_CONFIG_ENV_VAR),
    help="TOML configuration file",
)
@optgroup.group("SVD options", help="TODO")
@optgroup.option(
    "--svd-file",
    type=click.Path(exists=True, dir_okay=False, readable=True, path_type=Path),
    required=True,
    help="Path to the SVD file.",
)
# @click.option(
#     "--svd-options",
#     type=str,
#     help="Options fo"
# )


@optgroup.group("J-Link configuration", help="TODO")
@optgroup.option(
    "--lib",
    type=click.Path(exists=True, dir_okay=False, readable=True, path_type=Path),
    help="Path to a J-Link library to use.",
)
@optgroup.option("--serial", type=int, help="Serial number of the J-Link.")
@optgroup.option("--address", type=str, help="IP address and port of the J-Link.")
@optgroup.group("Commands", help="Commands to run. TODO: more description")
@optgroup.option("-i", "--input-file", type=click.File(mode="rt"), default=sys.stdin)
@optgroup.option("-o", "--output-file", type=click.File(mode="at"), default=sys.stdout)
@optgroup.option("-m", "--modified", is_flag=True)
@optgroup.option("-e", "--expand", is_flag=True)
@click.argument("command", required=False, nargs=-1)
def main(
    show: bool,
    lib: Optional[Path],
    serial: Optional[int],
    address: Optional[str],
    svd_file: Path,
    input_file: io.TextIOBase,
    output_file: io.TextIOBase,
    modified: bool,
    expand: bool,
    command: Sequence[str],
) -> int:
    # TODO: more than simple reads
    if command:
        raw_commands = command
    else:
        raw_commands = input_file.readlines()

    try:
        commands = parse_commands(raw_commands)
    except Exception:
        log.error("Parse Dies")
        return 1

    try:
        device = svd.parse(
            svd_file, options=svd.Options(parent_relative_cluster_address=True)
        )
    except Exception:
        log.error("SVD dies")
        return 1

    if show:
        if len(commands) > 1 or (commands and not isinstance(commands[0], ReadCommand)):
            log.error("--show requires zero or one path expression")
            return 1

    for cmd in commands:
        exec_command(device, cmd, modified=modified, expand=expand, show=show)

    return 0


# NOTE: device is only updated based on data read from jlink


def exec_command(device: svd.Device, command: Command, **kwargs: Any) -> None:
    if isinstance(command, ReadCommand):
        print_content(device, command.path, **kwargs)
        return
    else:
        print("bad command")


def print_content(
    device: svd.Device,
    path: FullEPath,
    *,
    modified: bool = False,
    expand: bool = False,
    **kwargs: Any,
) -> None:
    peripheral = device[path.periph]
    line_buffer = []
    last_address = -1
    # TODO: skip duplicate descriptions?
    # for example array elements
    for register in peripheral.register_iter(leaf_only=not expand):
        if path.reg and register.path.parts[: len(path.reg.parts)] != path.reg:
            continue

        if expand:
            indent = len(register.path) - (len(path.reg) if path.reg else 0)
        else:
            indent = 0

        addresses = (
            f"0x{register.address:08x}"
            if register.address != last_address
            else "         |"
        )

        # UGLY
        last_address = register.address

        name = register.name if expand else register.path

        description_str = register._spec.element.description
        description = f"- {description_str}" if description_str else ""

        content = (
            f"0x{register.content:08x}{'*' if register.modified else ' '}"
            if isinstance(register, svd.Register)
            else "            "
        )

        # To include:
        # - description
        # - RO/RW
        # - modified

        header = f"{addresses} {content} {'  ' * indent}"
        line = f"{header}{name} "  #  {description}"
        line_buffer.append(line)

        if isinstance(register, svd.Register):
            if modified and not register.modified:
                line_buffer.clear()
                continue

            for field in register.fields.values():
                name = f".{field.name}"
                content_value = field.content

                if field.enums:
                    for enum_name, enum_value in field.enums.items():
                        if content_value == enum_value:
                            content_enum = f" ({enum_name})"
                            break
                    else:
                        content_enum = " (illegal value for field)"
                else:
                    content_enum = ""

                field_content = (
                    f"0x{zero_pad(field.content, field.bit_width)}{content_enum}"
                )

                if expand:
                    bit_range = (
                        f"{field.bit_offset}:{field.bit_offset + field.bit_width - 1}>"
                        if field.bit_width > 1
                        else f"{field.bit_offset}>"
                    )

                    addresses = "         |"

                    line = f"{addresses}{' ' * len(content)}{'  ' * indent} {bit_range: >6} {name} = {field_content}"
                    line_buffer.append(line)

        for line in line_buffer:
            print(line)

            line_buffer.clear()


def zero_pad(content: int, max_width: int = 32) -> str:
    leading_zeros = "0" * ((max_width - content.bit_length()) // 4)
    value_str = f"{leading_zeros}{content:x}"
    return value_str


class FullEPath(NamedTuple):
    periph: str
    reg: Optional[svd.EPath]


class ReadCommand(NamedTuple):
    path: FullEPath


Command = Union[ReadCommand]


def parse_commands(commands: Sequence[str]) -> List[Command]:
    parsed = []

    for line in commands:
        for sub in (s.strip() for s in line.split(",")):
            parts = sub.split("->", maxsplit=1)
            if len(parts) == 2:
                periph, reg_str = parts
                reg = svd.EPath(reg_str)
            else:
                periph = parts[0]
                reg = None
            path = FullEPath(periph, reg)
            parsed.append(ReadCommand(path=path))

    return parsed


if __name__ == "__main__":
    main()
