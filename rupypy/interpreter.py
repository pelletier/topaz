from pypy.rlib import jit
from pypy.rlib.objectmodel import we_are_translated, specialize

from rupypy import consts


class Frame(object):
    _virtualizable2_ = ["locals_w[*]", "stack[*]", "stackpos"]

    def __init__(self, bytecode, w_self):
        self = jit.hint(self, fresh_virtualizable=True, access_directly=True)
        self.stack = [None] * bytecode.max_stackdepth
        self.locals_w = [None] * len(bytecode.locals)
        self.stackpos = 0
        self.w_self = w_self

    def push(self, w_obj):
        stackpos = jit.promote(self.stackpos)
        self.stack[stackpos] = w_obj
        self.stackpos = stackpos + 1

    def pop(self):
        stackpos = jit.promote(self.stackpos) - 1
        assert stackpos >= 0
        w_res = self.stack[stackpos]
        self.stack[stackpos] = None
        self.stackpos = stackpos
        return w_res

    def peek(self):
        stackpos = jit.promote(self.stackpos) - 1
        assert stackpos >= 0
        return self.stack[stackpos]

class Interpreter(object):
    jitdriver = jit.JitDriver(
        greens = ["pc", "bytecode"],
        reds = ["self", "frame"],
        virtualizables = ["frame"],
    )

    def interpret(self, space, frame, bytecode):
        pc = 0
        while True:
            self.jitdriver.jit_merge_point(
                self=self, bytecode=bytecode, frame=frame, pc=pc
            )
            instr = ord(bytecode.code[pc])
            pc += 1
            if instr == consts.RETURN:
                assert frame.stackpos == 1
                return frame.pop()

            if we_are_translated():
                for i, name in consts.UNROLLING_BYTECODES:
                    if i == instr:
                        pc = self.run_instr(space, name, consts.BYTECODE_NUM_ARGS[i], bytecode, frame, pc)
                        break
                else:
                    raise NotImplementedError
            else:
                pc = self.run_instr(space, consts.BYTECODE_NAMES[instr], consts.BYTECODE_NUM_ARGS[instr], bytecode, frame, pc)

    @specialize.arg(2, 3)
    def run_instr(self, space, name, num_args, bytecode, frame, pc):
        args = ()
        if num_args >= 1:
            args += (ord(bytecode.code[pc]),)
            pc += 1
        if num_args >= 2:
            args += (ord(bytecode.code[pc]),)
            pc += 1
        if num_args >= 3:
            raise NotImplementedError

        method = getattr(self, name)
        res = method(space, bytecode, frame, pc, *args)
        if res is not None:
            pc = res
        return pc

    def jump(self, bytecode, frame, cur_pc, target_pc):
        if target_pc < cur_pc:
            self.jitdriver.can_enter_jit(
                self=self, bytecode=bytecode, frame=frame, pc=target_pc,
            )
        return target_pc

    def LOAD_SELF(self, space, bytecode, frame, pc):
        frame.push(frame.w_self)

    def LOAD_CONST(self, space, bytecode, frame, pc, idx):
        frame.push(bytecode.consts[idx])

    def LOAD_LOCAL(self, space, bytecode, frame, pc, idx):
        frame.push(frame.locals_w[idx])

    def STORE_LOCAL(self, space, bytecode, frame, pc, idx):
        frame.locals_w[idx] = frame.peek()

    @jit.unroll_safe
    def SEND(self, space, bytecode, frame, pc, meth_idx, num_args):
        args_w = [frame.pop() for _ in range(num_args)]
        w_receiver = frame.pop()
        w_res = space.send(w_receiver, bytecode.consts[meth_idx], args_w)
        frame.push(w_res)

    def JUMP(self, space, bytecode, frame, pc, target_pc):
        return self.jump(bytecode, frame, pc, target_pc)

    def JUMP_IF_FALSE(self, space, bytecode, frame, pc, target_pc):
        if space.is_true(frame.pop()):
            return pc
        else:
            return self.jump(bytecode, frame, pc, target_pc)

    def DISCARD_TOP(self, space, bytecode, frame, pc):
        frame.pop()

    def UNREACHABLE(self, space, bytecode, frame, pc):
        raise Exception
    # Handled specially in the main dispatch loop.
    RETURN = UNREACHABLE