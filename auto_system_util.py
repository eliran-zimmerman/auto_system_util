import os
import sys
import subprocess
import re
import yaml
import math
from statistics import mean
import argparse
root = os.path.abspath(os.path.dirname(__file__))

parser = argparse.ArgumentParser('auto system')
parser.add_argument("--mode", default=1, type=int,
                    help="mode of system: 0: single code,  1: single socket,  2: multy socket")
args = parser.parse_args()

MAX_CACHE_PER_CORE = 2

lscpu_cmd = ['lscpu']
dmidecode_t17 = ['sudo', 'dmidecode', '-t17']
read_register = ['sudo', 'rdmsr', '0x620']
mlc_app = os.path.join(root, './mlc_3_9a/mlc')

mlc_cmds = {'L1': ['sudo', mlc_app, '--peak_injection_bandwidth', '-k0', '-b10'],
            'L2': ['sudo', mlc_app, '--peak_injection_bandwidth', '-k0', '-b200'],
            'LLC': ['sudo', mlc_app, '--peak_injection_bandwidth', '-k0', '-b10240'],
            'DDR': ['sudo', mlc_app, '--peak_injection_bandwidth', '-k0', '-b51200']
            }


class CPUSystem:
    def __init__(self, lscpu, ddr_dmidecode, mlc_l1, mlc_l2, mlc_llc, mlc_ddr):
        self.lscpu = lscpu
        self.ddr_dmidecode = ddr_dmidecode
        self.mlc_l1 = mlc_l1
        self.mlc_l2 = mlc_l2
        self.mlc_llc = mlc_llc
        self.mlc_ddr = mlc_ddr

    def calculate(self):
        self.core_l2_bw_read = self.mlc_l2.get_all_read_bw() / 1024
        self.ddr_bw_read = self.mlc_ddr.get_all_read_bw() / 1024
        self.llc_bw_read = self.mlc_llc.get_all_read_bw() / 1024
        self.core_l2_bw_rw = self.mlc_l2.get_1_1_bw() / 1024
        self.ddr_bw_rw = self.mlc_ddr.get_1_1_bw() / 1024
        self.llc_bw_rw = self.mlc_llc.get_1_1_bw() / 1024

    def finish(self):
        print("\n\n**********************************************")
        print(f"Number of CPU sockets     : {self.lscpu.get_num_sockets()}")
        print(f"DDR size per Socket       : {self.ddr_dmidecode.get_DDR_size()}")
        print(f"DDR BW Read               : {self.ddr_bw_read}")
        print(f"DDR BW Read+Write         : {self.ddr_bw_rw}")
        print(f"LLC size (per Socket)     : {self.lscpu.get_l3_cache_size_per_socket()}")
        print(f"LLC BW Read               : {self.llc_bw_read}")
        print(f"LLC BW Read+Write         : {self.llc_bw_rw}")
        print(f"L2 Cache size (per Core)  : {self.lscpu.get_l2_cache_size_per_core()}")
        print(f"L2 Cache BW Read          : {self.core_l2_bw_read}")
        print(f"L2 Cache BW Read+Write    : {self.core_l2_bw_rw}")
        print("**********************************************\n\n")


def replacenth(string, sub, wanted, n):
    where = [m.start() for m in re.finditer(sub, string)][n-1]
    before = string[:where]
    after = string[where:]
    after = after.replace(sub, wanted, 1)
    newString = before + after
    return newString


class Mlc:

    def __init__(self, lines):
        self.mlc_results = {'ALLReads': [],
                            '3-1Reads-Writes': [],
                            '2-1Reads-Writes': [],
                            '1-1Reads-Writes': []}
        for line in lines:
            line = line.rstrip("\n")
            if 'ALL Reads' in line:
                line = line.split(':')
                index = line[0]
                index = index.replace(" ", "")
                res = float(re.sub(r'\t', '', line[1]))
                self.mlc_results[index].append(res)
            elif '3:1 Reads-Writes' in line or '2:1 Reads-Writes' in line or '1:1 Reads-Writes' in line:
                line = replacenth(line, ':', '-', 1)
                line = line.split(':')
                index = line[0]
                index = index.replace(" ", "")
                res = float(re.sub(r'\t', '', line[1]))
                self.mlc_results[index].append(res)

    def get_all_read_bw(self):
        return mean(self.mlc_results['ALLReads'])

    def get_3_1_bw(self):
        return mean(self.mlc_results['3-1Reads-Writes'])

    def get_2_1_bw(self):
        return mean(self.mlc_results['2-1Reads-Writes'])

    def get_1_1_bw(self):
        return mean(self.mlc_results['1-1Reads-Writes'])


class LsCpu:
    def __init__(self, lines):
        for line in lines:
            line = line.rstrip("\n").replace("(", "_").replace(")", "")
            line = line.replace(" ", "")
            if line != '':
                line = line.split(':')
                setattr(self, line[0], line[1])

    def get_num_sockets(self):
        sockets = self.Socket_s
        return int(sockets)


    def get_cpu_per_socket(self):
        cpus = self.Core_spersocket
        return int(cpus)

    def get_numa_node0_cpu(self):
        node0 = self.NUMAnode0CPU_s
        node0 = node0.split(",")
        return node0[0]

    def get_max_freq(self):
        max_mhz = self.CPUmaxMHz
        # return in GHZ
        return float(max_mhz) / 1000

    @staticmethod
    def _calc_size(size):
        mult = 1
        if ('MiB' in size) or ('M' in size):
            size = size.replace("MiB", "").replace("M", "")
        elif ("K" in size) or ("KiB" in size):
            size = size.replace("KiB", "").replace("K", "")
            mult = 1 / 1024
        # return all Cache sizes in MB
        return math.ceil(float(size)) * mult

    def get_l1_cache_size(self):
        size = self.L1icache
        return self._calc_size(size)

    def get_l2_cache_size_per_core(self):
        size = self.L2cache
        size = self._calc_size(size)
        # check lscpu value: sometimes return aggrigated value for all Cores
        if size > MAX_CACHE_PER_CORE:
            size = size/self.get_cpu_per_socket()/self.get_num_sockets()
        return size

    def get_l3_cache_size_per_socket(self):
        size = self.L3cache
        return self._calc_size(size) / self.get_num_sockets()


class DDRDmidecode:
    def __init__(self, lines):
        for i in range(len(lines)):
            if "Handle" in lines[i] and "type" in lines[i] and "17" in lines[i]:
                line = lines[i].rstrip("\t").rstrip("\n").replace(" ", "_").replace(',', "")
                setattr(self, "self." + line, {})
                for j in range(i+2, i+23):
                    line_splited = lines[j].split(":")
                    name = re.sub(r'\t', '', line_splited[0])
                    value = line_splited[1].rstrip("\n")
                    getattr(self, "self."+line)[name] = value

        self.ref_dimm_attr = None
        self.handles = 0
        self.num_of_DDR_slots, self.num_of_DDR_dimms = self.extract_num_of_DDR_dimms()


    def extract_num_of_DDR_dimms(self):
        bank_dict = {}
        bank_list = []
        local_attr = None
        called = False
        for attr in self.__dict__:
            if "Handle" not in attr:
                continue
            if getattr(self, attr)['Manufacturer'] == ' NO DIMM':
                continue
            self.handles += 1
            bank_locator = getattr(self, attr)['Locator']
            bank_locator = bank_locator.split("_")
            bank_locator = bank_locator[0].split("-")
            if bank_locator[0] not in bank_list:
                bank_dict[bank_locator[0]] = 1
                bank_list.append(bank_locator[0])
                if not called:
                    local_attr = attr
                    called = True
                continue
            else:
                bank_dict[bank_locator[0]] += 1
        self.ref_dimm_attr = local_attr

        def first_item(items):
            for n, v in items:
                return v
        return len(bank_dict), first_item(bank_dict.items())

    def get_num_of_DDR_DIMMs(self):
        return self.num_of_DDR_dimms

    def get_DDR_speed(self):
        speed = getattr(self, self.ref_dimm_attr)['Speed']
        speed = speed.split(" ")
        speed = int(speed[1])
        return speed

    def get_DDR_size(self):
        size = getattr(self, self.ref_dimm_attr)['Size']
        size = size.split(" ")

        mult = 1
        if 'KB' in size[2]:
            mult = 1 / 1024
        elif 'GB' in size[2]:
            mult = 1024
        size = int(size[1]) * mult
        # return in MB
        return size * self.handles / self.num_of_DDR_slots


class UncoreClock:
    def __init__(self, line):
        self.max = 0
        self.min = 0
        line = re.sub(r'\t', '', line[0])
        value = line.rstrip("\n")
        if len(value) == 4:
            self.min = int(value[0] + value[1], 16)
            self.max = int(value[2] + value[3], 16)
        elif len(value) == 3:
            self.min = int('0'+value[0], 16)
            self.max = int(value[1]+value[2], 16)
        elif len(value) == 2:
            self.max = int(value[0] + value[1], 16)

    def get_max_clock(self):
        return self.max / 10

    def get_min_clock(self):
        return self.min / 10


def execute_cmd(cmd):
    lines = []
    p1 = subprocess.Popen(cmd, stdout=subprocess.PIPE, universal_newlines=True)
    while True:
        nextline = p1.stdout.readline()
        if nextline == '' and p1.poll() is not None:
            break
        # sys.stdout.write(nextline)
        sys.stdout.flush()
        lines.append(nextline)

    output = p1.communicate()[0]
    exitCode = p1.returncode

    if (exitCode == 0):
        p1.terminate()
        return lines
    else:
        raise Exception(cmd, exitCode, output)


def update_mlc_cmd(lscpu):
    global mlc_cmds
    l1_size = int(lscpu.get_l1_cache_size() * 1024)  # to get it in KB
    l2_size = int(lscpu.get_l2_cache_size_per_core() * 1024)  # to get it in KB
    llc_size = int(lscpu.get_l3_cache_size_per_socket() * 1024)  # to get it in KB
    mlc_cmds['L1'][len(mlc_cmds['L1'])-1] = '-b'+str(l1_size - int(l1_size*0.150))
    mlc_cmds['L2'][len(mlc_cmds['L2']) - 1] = '-b' + str(l2_size - int(l2_size*0.15))
    if args.mode == 0:  # single core
        mlc_cmds['LLC'][len(mlc_cmds['LLC']) - 1] = '-b' + str(llc_size - int(llc_size * 0.05))
        mlc_cmds['DDR'][len(mlc_cmds['DDR']) - 1] = '-b' + str(llc_size + int(llc_size * 0.50))
        mlc_cmds['LLC'][len(mlc_cmds['LLC']) - 1] = '-b' + str(llc_size - int(llc_size * 0.15))
        mlc_cmds['DDR'][len(mlc_cmds['DDR']) - 1] = '-b' + str(llc_size + int(llc_size * 0.50))
    elif args.mode == 1:  # single socket
        mlc_cmds['LLC'][len(mlc_cmds['LLC']) - 1] = '-b' + str(int((llc_size - int(llc_size * 0.05))/lscpu.get_cpu_per_socket()))
        mlc_cmds['DDR'][len(mlc_cmds['DDR']) - 1] = '-b' + str(int(llc_size))
        mlc_cmds['LLC'][len(mlc_cmds['LLC']) - 2] = '-k' + lscpu.get_numa_node0_cpu()
        mlc_cmds['DDR'][len(mlc_cmds['DDR']) - 2] = '-k' + lscpu.get_numa_node0_cpu()
    elif args.mode == 2:  # multy socket
        mlc_cmds['LLC'][len(mlc_cmds['LLC']) - 1] = '-b' + str(llc_size - int(llc_size * 0.15))
        mlc_cmds['DDR'][len(mlc_cmds['DDR']) - 1] = '-b' + str(llc_size + int(llc_size * 0.50))
        mlc_cmds['LLC'][len(mlc_cmds['LLC']) - 1] = '-b' + str(llc_size - int(llc_size * 0.15))
        mlc_cmds['DDR'][len(mlc_cmds['DDR']) - 1] = '-b' + str(llc_size + int(llc_size * 0.50))


mlc_for_avg = 1
out = execute_cmd(lscpu_cmd)
out1 = execute_cmd(dmidecode_t17)
dmi = DDRDmidecode(out1)
lscpu = LsCpu(out)
update_mlc_cmd(lscpu)
mlc_l1_lines = []
print("Start sampling ....")
for i in range(mlc_for_avg):
    mlc_l1_lines += execute_cmd(mlc_cmds['L1'])
mlc_l1 = Mlc(mlc_l1_lines)
mlc_l2_lines = []
for i in range(mlc_for_avg):
    mlc_l2_lines += execute_cmd(mlc_cmds['L2'])
mlc_l2 = Mlc(mlc_l2_lines)
mlc_llc_lines = []
for i in range(mlc_for_avg):
    mlc_llc_lines += execute_cmd(mlc_cmds['LLC'])
mlc_llc = Mlc(mlc_llc_lines)
mlc_ddr_lines = []
for i in range(mlc_for_avg):
    mlc_ddr_lines += execute_cmd(mlc_cmds['DDR'])
mlc_ddr = Mlc(mlc_ddr_lines)
cpu_sys = CPUSystem(lscpu, dmi, mlc_l1, mlc_l2, mlc_llc, mlc_ddr)
cpu_sys.calculate()
cpu_sys.finish()

