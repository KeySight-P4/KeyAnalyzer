import json
import sys

# TODO: The drop primitive should be differently handled in the ingress pipeline and the egress pipeline


NON_DETERMINISTIC_OBJECTS = ['ingress_global_timestamp', 
                             'enq_timestamp',
                             'enq_qdepth',
                             'deq_timedelta',
                             'deq_qdepth']


def resolve_cascade_expression(exp):
    if isinstance(exp, int):
        return []
    value = exp['value']
    tmp = []

    if exp is None:
        return []
    elif exp['type'] == 'expression':
        right = value['right']
        left = value['left']

        if left is not None:
            tmp.extend(resolve_cascade_expression(left))
        if right is not None:
            tmp.extend(resolve_cascade_expression(right))
    elif exp['type'] == 'field':
        tmp.append(value)
    elif exp['type'] == 'header':
        tmp.append([value, 'valid'])
    else:
        return []
    return tmp


class P4Model:
    '''
    The abstraction model of P4 pipeline
    '''
    def __init__(self, p4ir):
        p4prog = open(p4ir, 'r')
        if p4prog is None:
            print '%d is not a valid json-based IR file for a P4 program' % sys.argv[1]
            exit(1)
        self.ir = json.load(p4prog)
        self.header_types = []
        self.header_type_map = {}
        for ht in self.ir['header_types']:
            self.header_types.append(ht['name'])
            self.header_type_map[ht['name']] = ht

        self.headers = [] 
        self.header_map = {}
        self.fields = []

        for h in self.ir['headers']:
            self.headers.append(h['name'])
            self.header_type_map[h['name']] = h['header_type']
            ht = self.header_type_map[h['header_type']]
            if isinstance(ht, str):
                for f in ht['fields']:
                    self.fields.append([h['name'], f[0]])

        self.calculations = []
        self.calculation_dict = {}
        for cal in self.ir['calculations']:
            self.calculations.append(cal)
            self.calculation_dict[cal['name']] = cal

        self.tables = []
        self.conditionals = []
        self.init_tables = []
        self.table_dict = {}
        self.conditional_dict = {}
        self.table_pipe_dict = {}
        pipe_id = 0

        for pipe in self.ir['pipelines']:
            if pipe['init_table'] is None:
                break
            self.tables.extend(pipe['tables'])
            self.conditionals.extend(pipe['conditionals'])
            self.init_tables.append(pipe['init_table'])

            for t in pipe['tables']:
                self.table_pipe_dict[t['name']] = pipe_id
                self.table_dict[t['name']] = t
            for c in pipe['conditionals']:
                self.table_pipe_dict[c['name']] = pipe_id
                self.conditional_dict[c['name']] = c
            pipe_id = pipe_id + 1

        self.dag = {}

        for t in self.tables:
            name = t['name']
            self.dag[name] = self.next_tables(name)

        for c in self.conditionals:
            name = c['name']
            self.dag[name] = []
            if c['true_next'] is not None:
                self.dag[name].append(c['true_next'])
            if c['false_next'] is not None:
                self.dag[name].append(c['false_next'])

        self.actions = []
        self.action_dict = {}

        for a in self.ir['actions']:
            self.actions.append(a)
            self.action_dict[a['name']] = a
        self.field_bits = {}

        self.registers = []
        for r in self.ir['register_arrays']:
            self.registers.append(r['name'])

        self.counters = []
        for c in self.ir['counter_arrays']:
            self.counters.append(c['name'])
        
        self.meters = []
        for m in self.ir['meter_arrays']:
            self.meters.append(m['name'])

        self.header_stack = []
        self.header_stack_dict = {}
        for m in self.ir['header_stacks']:
            for k in range(m['size']):
                self.header_stack.append('%s[%d]'%(m['name'], k))
                self.header_stack_dict['%s[%d]'%(m['name'], k)] = m['header_type']
        
        p4prog.close()

    def get_field_bits(self, table_name, field_name):
        field = table_name+'.'+field_name
        if field in self.field_bits:
            return self.field_bits[field]
        if field_name == 'valid':
            return 1
        header_type = None
        for h in self.ir["headers"]:
            if h["name"] == table_name:
                header_type = h["header_type"]
        for h in self.ir["header_types"]:
            if h["name"] == header_type:
                for f in h["fields"]:
                    if f[0] == field_name:
                        self.field_bits[field] = f[1]
                        if f[1] == '*':
                            return 0
                        return f[1]
        if table_name in self.meters + self.counters + self.registers:
            return 32


    def get_primitive_actions(self, name):
        if name in self.action_dict:
            act = self.action_dict[name]
            return act['primitives']
        else:
            return []

    def get_table_names(self):
        return self.table_dict.keys()

    def get_field_list_from_calculation(self, name):
        if name not in self.calculation_dict:
            return []
        field_list = []
        cal = self.calculation_dict[name]
        for i in cal['input']:
            if i['type'] == 'field':
                field_list.append(i['value'])
        return field_list

    def get_table(self, name):
        '''
        Get the table json object according to table name.
        :param name: table name
        :return: the table json object
        '''
        if name in self.table_dict:
            return self.table_dict[name]
        return None

    def get_table_actions(self, name):
        '''
        Get the action list of a table
        :param name: the table name
        :return: the action list
        '''
        if name not in self.table_dict:
            return []
        table = self.table_dict[name]
        return table['actions']

    def get_conditional(self, name):
        '''
        Get the conditional json object according to name
        :param name: the conditional name
        :return: conditional json object
        '''
        if name in self.conditional_dict:
            return self.conditional_dict[name]
        return None

    def get(self, name):
        '''
        Get the json object according to name
        :param name: name of the json object
        :return: the json object
        '''
        if name in self.table_dict:
            return self.table_dict[name]
        if name in self.conditional_dict:
            return self.conditional_dict[name]
        return None

    def next_tables(self, name):
        '''
        Get the next tables
        :param name: the current table
        :return: a list of next tables
        '''
        t = self.get_table(name)
        if t is None:
            return []
        ret = []
        for k in t['next_tables']:
            if t['next_tables'][k] is not None:
                ret.append(t['next_tables'][k])
        if len(ret) == 0:
            pipe_id = self.table_pipe_dict[name]
            if len(self.ir['pipelines']) > pipe_id + 1:
                if self.ir['pipelines'][pipe_id + 1]['init_table'] is not None:
                    ret.append(self.ir['pipelines'][pipe_id + 1]['init_table'])
        ret = union(ret)
        return ret

    def reversed_topological_sort(self):
        color_map = {}
        timer_map = {}
        for t in self.tables + self.conditionals:
            color_map[t['name']] = 0
            timer_map[t['name']] = [-1, -1]

        def topo_sort(curr_name, timer):
            color_map[curr_name] = 1
            timer_map[curr_name][0] = timer
            timer = timer + 1
            for t in self.dag[curr_name]:
                if t not in color_map:
                    print 'Exist due to %s of %s not in color map.'%(t, curr_name)
                    exit(1)
                if color_map[t] == 0:
                    timer = topo_sort(t, timer)
            color_map[curr_name] = 2
            timer_map[curr_name][1] = timer
            return timer + 1
        table_list = []
        for t in self.init_tables:
            tmp = topo_sort(t, 0)
            x = xrange((tmp-1)/2, tmp, 1)
            y = []
            for i in xrange(len(x)):
                y.append(x[len(x) - 1 - i])
            for i in y:
                for t in timer_map:
                    if timer_map[t][1] == i and t not in table_list:
                        table_list.append(t)
        y = []  
        for i in range(len(table_list)):
            y.append(table_list[len(table_list) - 1 - i])
        return table_list[::-1]

    def get_action_parameters(self, name):
        if name not in self.action_dict:
            return []
        else:
            return [p['name'] for p in self.action_dict[name]['runtime_data']]

    def get_inputs(self, name):
        '''
        Get the input of a specified node
        :param name: the node (conditional/table) name
        :return: the input object set or the read object set
        '''
        if name in self.conditional_dict:
            ret = []
            for i in resolve_cascade_expression(self.conditional_dict[name]['expression']):
                if i not in self.get_non_deterministic_objects():
                    ret.append(i)
            return ret
        if name in self.table_dict:
            t = self.table_dict[name]
            inputs = []
            # Resolve match keys
            for k in t['key']:
                if k['match_type'] == 'valid':
                    inputs.append([k['target'], 'valid'])
                else:
                    inputs.append(k['target'])
            # Resolve actions
            for a in t['actions']:
                inputs.extend(self.get_action_inputs(a))

            ret = []
            for i in inputs:
                if i not in self.get_non_deterministic_objects():
                    ret.append(i)

            return ret

    def get_field_list(self, id):
        field_list = []
        for fl in self.ir['field_lists'][id]['elements']:
            field_list.append(fl['value'])
        return field_list

    def get_table_action_list(self):
        ret = []
        for t in self.tables:
            for a in t['actions']:
                ret.append([t['name'], a])
        return ret

    def get_non_deterministic_objects(self):
        ret = []
        for f in self.fields:
            if f[1] in NON_DETERMINISTIC_OBJECTS:
                ret.append(f)
        return ret

    def get_conditional_inputs(self):
        '''
        Get the inputs of all conditionals
        :return: input field list and the conditional name
        '''
        conditional_list = []
        field_list = []
        for c in self.conditionals:
            conditional_list.append(c['name'])
            field_list.extend(self.get_inputs(c['name']))

        return field_list, conditional_list

    # Get primitive inputs according to P4 Spec
    def get_primitive_inputs(self, primitive):
        if primitive['op'] == 'modify_field':
            input_fields = []
            for input in primitive['parameters']:
                if input['type'] == 'field':
                    input_fields.append(input['value'])
                elif input['type'] == 'expression':
                    input_fields.extend(resolve_cascade_expression(input['value']))
                elif input['type'] == 'runtime_data' or input['type'] == 'hexstr':
                    input_fields.append([input['type'], input['value']])
                elif input['type'] == 'register':
                    input_fields.extend(resolve_cascade_expression(input['value'][1]))
                else:
                    print 'Unresolved type: ' + input['type'] + ' in get_primitive_inputs.'
                    exit(1)
            return input_fields
        elif primitive['op'] == 'copy_header':
            return []
        elif primitive['op'] == 'drop':
            return []
        elif primitive['op'] in ['remove_header', 'add_header', 'pop', 'push', 'recirculate'] :
            return []
        elif primitive['op'] == 'add_to_field':
            input_fields = []
            for input in primitive['parameters']:
                if input['type'] == 'field':
                    input_fields.append(input['value'])
                elif input['type'] == 'expression':
                    input_fields.extend(resolve_cascade_expression(input['value']))
                elif input['type'] == 'runtime_data' or input['type'] == 'hexstr':
                    input_fields.append([input['type'], input['value']])
                else:
                    print 'Unresolved type: ' + input['type'] + ' in get_primitive_inputs.'
                    exit(1)
            return input_fields
        elif primitive['op'] in ['clone_ingress_pkt_to_egress',
                                 'clone_ingress_pkt_to_ingress',
                                 'clone_egress_pkt_to_egress',
                                 'clone_egress_pkt_to_ingress']:
            i = primitive['parameters'][1]['value']
            i = int(i, 16) - 1
            return self.get_field_list(i)
        elif primitive['op'] == 'register_write':
            register = primitive['parameters'][0]
            input_fields = []
            index = primitive['parameters'][1]
            if index['type'] == 'field':
                input_fields.append(index['value'])
            elif index['type'] == 'expression':
                input_fields.extend(resolve_cascade_expression(index['value']))
            elif index['type'] == 'runtime_data':
                input_fields.append([[register['value'], 'index']])
            elif index['type'] == 'hexstr':
                input_fields.append([index['type'], index['value']])
            else:
                print 'Unresolved type: ' + index['type'] + ' in get_primitive_inputs.'
                exit(1)
            field = primitive['parameters'][1]
            if field['type'] == 'field':
                input_fields.append(field['value'])
            elif field['type'] == 'expression':
                input_fields.extend(resolve_cascade_expression(field['value']))
            elif index['type'] in ['runtime_data', 'hexstr']:
                input_fields.append([field['type'], field['value']])
            else:
                print 'Unresolved type: ' + field['type'] + ' in get_primitive_inputs.'
                exit(1)
            return input_fields
        elif primitive['op'] == 'modify_field_with_hash_based_offset':
            return self.get_field_list_from_calculation(primitive['parameters'][2]['value'])
        elif primitive['op'] == 'register_read':
            register = primitive['parameters'][1]
            input = primitive['parameters'][2]
            if input['type'] == 'field':
                return [input['value']]
            elif input['type'] == 'expression':
                return resolve_cascade_expression(input['value'])
            elif input['type'] == 'runtime_data':
                return  [[register['value'], 'index']]
            elif input['type'] == 'hexstr':
                return [[input['type'], input['value']]]
            else:
                print 'Unresolved type: ' + input['type'] + ' in get_primitive_inputs.'
                exit(1)
        elif primitive['op'] in ['bit_xor', 'bit_and', 'bit_or', 'bit_and', 'subtract', 'add', 'shift_left']: # bit_xor(dest, value1, value2)
            inputs = []
            value1 = primitive['parameters'][1]
            if value1['type'] in ['field']:
                inputs.append(value1['value'])
            value2 = primitive['parameters'][2]
            if value2['type'] in ['field']:
                inputs.append(value2['value'])
            return inputs
        elif primitive['op'] in ['subtract_from_field'] :
            inputs = []
            value1 = primitive['parameters'][1]
            if value1['type'] in ['field']:
                inputs.append(value1['value'])
            return inputs
        elif primitive['op'] in ['count', 'execute_meter']:
            index = primitive['parameters'][1]
            if index['type'] in ['field']:
                return [index['value']]
            else:
                return [[primitive['parameters'][0]['value'], 'index']]
        elif primitive['op'] in ['generate_digest',
                                'resubmit',
                                'truncate', 'no_op', 'pop', 'modify_field_rng_uniform']:
            return []
        else:
            print 'Unresolved op: ' + primitive['op'] + ' in get_primitive_inputs.'
            exit(1)

    def get_primitive_outputs(self, primitive):
        if primitive['op'] in ['modify_field', 'add_to_field', 'modify_field_with_hash_based_offset', 'shift_left']:
            output = primitive['parameters'][0]

            if output['type'] == 'field':
                return [output['value']]
            elif output['type'] == 'register':
                value = output['value'][1]
                if value['type'] == 'field':
                    return [value['value']]
                else:
                    return []
            else:
                print 'Unresolved type: ' + output['type'] + ' in get_primitive_outputs.'
                exit(1)
        elif primitive['op'] == 'copy_header':
            return []
        elif primitive['op'] == 'drop':
            return [['standard_metadata', 'egress_spec']]
        elif primitive['op'] in ['remove_header', 'add_header']:
            output = primitive['parameters'][0]
            return [[output['value'], 'valid']]
        elif primitive['op'] in ['clone_ingress_pkt_to_egress',
                                 'clone_ingress_pkt_to_ingress',
                                 'clone_egress_pkt_to_egress',
                                 'clone_egress_pkt_to_ingress',
                                 'register_write',
                                 'count',
                                 'generate_digest',
                                 'resubmit',
                                 'truncate', 'no_op', 'pop', 'push', 'recirculate', 'modify_field_rng_uniform']:
            return []
        elif primitive['op'] in ['register_read']:
            return [primitive['parameters'][0]['value']]
        elif primitive['op'] in ['bit_xor', 'bit_or', 'add', 'subtract_from_field', 'bit_and', 'subtract']:
            return [primitive['parameters'][0]['value']]
        elif primitive['op'] in ['execute_meter']:
            return [primitive['parameters'][2]['value']]
        else:
            print 'Unresolved op: ' + primitive['op'] + ' in get_primitive_outputs.'
            exit(1)

    def get_action_outputs(self, name):
        if name not in self.action_dict:
            return []
        outputs = []
        action = self.action_dict[name]
        for primitive in action['primitives']:
            outputs.extend(self.get_primitive_outputs(primitive))
        return outputs

    def get_action_inputs(self, name):
        if name not in self.action_dict:
            return []
        inputs = []
        action = self.action_dict[name]
        for primitive in action['primitives']:
            for input in self.get_primitive_inputs(primitive):
                if input[0] not in ['runtime_data', 'hexstr']:
                    inputs.append(input)
        return inputs

    def get_outputs(self, name):
        if name in self.conditional_dict:
            return []
        if name in self.table_dict:
            t = self.table_dict[name]
            outputs = []
            # Resolve actions
            for a in t['actions']:
                outputs.extend(self.get_action_outputs(a))
            outputs = union(outputs)
            ret = []
            for o in outputs:
                if o not in self.get_non_deterministic_objects():
                    ret.append(o)
            return ret
        return []

    def get_exact_match_field(self):
        '''
        Get the tables and fields with the range match type
        :return: the field list and the table list
        '''
        field_list = []
        table_list = []
        for t in self.tables:
            for key in t['key']:
                if key['match_type'] == 'exact':
                    if key['target'] not in field_list:
                        field_list.append(key['target'])
                    if t['name'] not in table_list:
                        table_list.append(t['name'])
        return field_list, table_list

    def get_lpm_match_fields(self):
        '''
        Get the tables and fields with the range match type
        :return: the field list and the table list
        '''
        field_list = []
        table_list = []
        for t in self.tables:
            for key in t['key']:
                if key['match_type'] == 'lpm':
                    if key['target'] not in field_list:
                        field_list.append(key['target'])
                    if t['name'] not in table_list:
                        table_list.append(t['name'])
        return field_list, table_list

    def get_range_match_fields(self):
        '''
        Get the tables and fields with the range match type
        :return: the field list and the table list
        '''
        field_list = []
        table_list = []
        for t in self.tables:
            for key in t['key']:
                if key['match_type'] == 'range':
                    if key['target'] not in field_list:
                        field_list.append(key['target'])
                    if t['name'] not in table_list:
                        table_list.append(t['name'])
        return field_list, table_list

    def is_table(self, name):
        if name in self.table_dict:
            return True
        else:
            return False


def union(x1, x2 = [], x3 = [], x4 = [], x5 = []):
    d = []
    for x in x1 + x2 + x3 + x4 + x5:
        if x not in d:
            d.append(x)
    return d


def intersection(x1, x2, x3 = None, x4 = None, x5 = None):
    d = []
    for x in x1:
        if x not in d and x in x2:
            if x3 is not None:
                if x in x3:
                    if x4 is not None:
                        if x in x4:
                            if x5 is not None:
                                if x in x5:
                                    d.append(x)
                            else:
                                d.append(x)
                    else:
                        d.append(x)
            else:
                d.append(x)
    d = union(d)
    return d


def difference(x1, x2):
    d = []
    for x in x1:
        if x[0:2] not in x2:
            d.append(x)
    return d


# Oringal algorithm to generate key structure
def field_replication(model):
    V = model.reversed_topological_sort()
    dup_dict = {}
    p_dup_dict = {}
    c_output_dict = {}
    ps = []
    for v in V:
        dup = [] # Duplicate the object in current table
        p_dup = [] # Defer the object duplication to previous tables
        c_outputs = model.get_outputs(v) # Accumulative outputs
        outputs = model.get_outputs(v)
        inputs = model.get_inputs(v)
        for a in model.dag[v]:
            # Action dependency
            if a not in c_output_dict:
                c_output_dict[a] = []
            for x in intersection(outputs, c_output_dict[a]):
                dup.append(x + [v])
                
            # Reverse-match dependency
            for x in intersection(inputs, c_output_dict[a]):
                p_dup.append(x + [v])
            c_outputs = union(c_outputs, c_output_dict[a])
        for a in model.dag[v]:
            if a not in p_dup_dict:
                p_dup_dict[a] = []
            for x in p_dup_dict[a]:
                found_flag = 0
                for m in outputs:
                    if m[0] == x[0] and m[1] == x[1]:
                        for y in dup:
                            if m[0] == y[0] and m[1] == y[1]:
                                found_flag = 1
                                break
                        if found_flag == 0:
                            dup.append(m + [v])
                        found_flag = 1
                        break
                if found_flag == 0:
                    p_dup.append(x)
        dup_dict[v] = dup
        p_dup_dict[v] = p_dup
        c_output_dict[v] = c_outputs
        ps = union(ps, dup, inputs, outputs)
    if V[-1] in p_dup_dict:
        ps = ps + p_dup_dict[V[-1]]

    return ps, dup_dict, p_dup_dict[V[-1]]


def generate_keysight_start(p_dup):
    '''
    Generate KeySight start table and action
    :param model:
    :param p_dup:
    :return:
    '''
    table = 'table keysight_start {\n' \
            '\tactions {\n' \
            '\t\tkey_start;\n' \
            '\t}\n' \
            '}\n'
    action = 'action keysight_start () {\n'
    for p in p_dup:
        action += '\tmodify_field(keysight_metadata.%s_%s_start, %s.%s);\n'%(p[0], p[1], p[0], p[1])
    action += '}\n'
    return table + action


def generate_keysight_metadata(model, ks, dup, p_dup):
    '''
    Generate keysight metadata fields including duplicated objects and protocol valid bits
    :param model: the P4 pipeline moel
    :param ps: the postcard structure
    :param dup: the duplication dictionary
    :return:
    '''
    metadata = 'header_type keysight_metadata_t {\n'
    for t in model.get_table_names():
        metadata += '\t%s : %d;\n'%(t, 4)

    for p in p_dup:
        metadata += '\t%s_%s_start : %d;\n'%(p[0], p[1], model.get_field_bits(p[0], p[1]))
    for p in ks:
        if p[1] == 'valid':
            metadata += '\t%s_valid : 1;\n'%(p[0])
        #elif len(p) == 2:
        #    metadata += '\t%s_%s : %d;\n'%(p[0], p[1], model.get_field_bits(p[0], p[1]))
    for t in dup:
        for d in dup[t]:
            metadata += '\t%s_%s_%s : %d;\n'%(t, d[0], d[1], model.get_field_bits(d[0], d[1]))
    metadata += '}\n'
    metadata += 'metadata keysight_metadata_t keysight_metadata;\n'
    return metadata


def generate_keyfilter_field_list(model, ks):
    '''
     Generate keysight metadata fields including duplicated objects and protocol valid bits
     :param model: the P4 pipeline moel
     :param ps: the postcard structure
     :param dup: the duplication dictionary
     :return:
     '''
    metadata = 'filed_list keyfilter_field_list {\n'
    for t in model.get_table_names():
        metadata += '\tkeysight_metadata.%s\n'%(t)

    for p in ks:
        if len(p) == 2:
            metadata += '\t%s.%s;\n' % (p[0], p[1])
        if len(p) == 3:
            metadata += '\tkeysight_metadata.%s_%s_%s;\n' % (p[0], p[1], p[2])

    metadata += '};\n'
    return metadata


def generate_keysight_actions(model, ps, dup):
    keysight_actions = []
    modified_actions = []

    tables = model.get_table_names()

    for t in tables:
        actions = model.get_table_actions(t)
        tacount = 1
        for a in actions:
            outputs = model.get_action_outputs(a)
            record_list = []
            for d in dup[t]:
                if d[0:2] in outputs:
                    record_list.append(d)
            action_str = 'action %s_%s('%(t, a)
            modified_actions.append('%s_%s'%(t, a))
            parameters = model.get_action_parameters(a)
            for p in parameters:
                action_str += '%s,'%p

            action_str = action_str.strip(',')
            action_str += ') {\n'
            action_str += '\t%s('%a
            for p in parameters:
                action_str += '%s,'%p
            action_str = action_str.strip(',')
            action_str += ');\n'

            action_str += '\tmodify_field(keysight_metadata.%s, %d)' % (t, tacount)
            tacount += 1

            action = model.action_dict[a]
            for record in record_list:
                for p in action['primitives']:
                    for out in model.get_primitive_outputs(p):
                        if out[0] == record[0] and out[1] == record[1]:
                            for tmp in model.get_primitive_inputs(p):
                                action_str += '\tmodify_field(keysight_metadata.%s_%s_%s, ' % (record[0], record[1], record[2])
                                if tmp[0] == 'runtime_data':
                                    action_str += '%s);\n'%(parameters[tmp[1]])
                                elif tmp[0] == 'hexstr':
                                    action_str += '%s);\n' % (tmp[1])
                                else:
                                    action_str += '%s.%s);\n' % (tmp[0], tmp[1])
                                break

            action_str += '}\n'
            keysight_actions.append(action_str)

    action_str = 'action keysight_report(switch_id, path_id) {\n'
    action_str += '\tadd_header(keysight_header);\n'
    action_str += '\tmodify_field(keysight_header.switch_id, switch_id);\n'
    action_str += '\tmodify_field(keysight_header.path_id, path_id);\n'
    for t in model.get_table_names():
        action_str += '\tmodify_field(keysight_header.%s, keysight_metadata.%s);\n' % (t, t)

    for p in ps:
        if len(p) == 2:
            action_str += '\tmodify_field(keysight_header.%s_%s, %s.%s);\n'%(p[0], p[1], p[0], p[1])
        if len(p) == 3:
            action_str += '\tmodify_field(keysight_header.%s_%s_%s, keysight_metadata.%s_%s_%s);\n'%(p[0], p[1], p[2], p[0], p[1], p[2])
    action_str += '}\n'
    keysight_actions.append(action_str)

    return keysight_actions


def generate_keysight_report_header(model, ps):
    padding_id = 0
    header = 'header_type keysight_header_t {\n'
    header += '\tswitch_id : 32;\n'
    header += '\tpath_id : 32;\n'
    for t in model.get_table_names():
        header += '\t%s : 4;\n'%(t)
    if len(model.get_table_names()) % 2 == 1:
        header += '\tpadding_%d : 4;\n' % (padding_id)
        padding_id += 1

    for p in ps:
        l = 0
        tmp = '\t%s'%p[0]
        if len(p) == 1:
            l = 8
        else:
            l = model.get_field_bits(p[0], p[1])
            if l is None:
                print "Error: %s.%s"%(p[0], p[1])
            for x in p[1:]:
                tmp += '_%s' % x

        header += '%s : %d;\n'%(tmp, l)
        if l % 8 != 0:
            header += '\tpadding_%d : %d;\n'%(padding_id, (l + 7) / 8 * 8 - l)
            padding_id += 1
    header += '}\n'
    header += 'header keysight_header_t keysight_header;\n'
    return header


def normalized_key(model, ps):
    ks = []
    # Search the range fields
    range_fields, range_tables = model.get_range_match_fields()
    exact_fields, exact_tables = model.get_exact_match_field()
    lpm_fields, lpm_tables = model.get_lpm_match_fields()

    # Search the if-else statement
    conditional_fields, conditionals = model.get_conditional_inputs()

    action_fields = []
    oridinary_action_fields = []
    actions = []
    # Search the modify_field
    for a in model.actions:
        for p in a['primitives']:
            o = model.get_primitive_outputs(p)
            i = model.get_primitive_inputs(p)
            if len(i) > 0:
                action_fields.extend(o)
                action_fields.extend(i)
                if a['name'] not in actions:
                    actions.append(a['name'])
            else:
                oridinary_action_fields.extend(o)

    for p in ps:
        if p[0:2] in range_fields + conditional_fields + action_fields:
            if p[0:2] not in exact_fields + lpm_fields + oridinary_action_fields:
                continue
        ks.append(p)

    return ks, range_tables, conditionals


def ps_merge(model, ks):
    ps = []
    for f in ks:
        ps.append(f)
    for t in model.get_table_names():
        ps.append([t])
    return ps


def generate_keysight_p4(file_name, model, ks, dup, p_dup, ps):
    f = open(file_name, 'w')
    for a in generate_keysight_actions(model, ks, dup):
        f.write(a)
    f.write(generate_keysight_metadata(model, ks, dup, p_dup))
    f.write(generate_keyfilter_field_list(model, ks))
    f.write(generate_keysight_start(p_dup))
    f.write(generate_keysight_report_header(model, ps))

    f.close()


def generate_keysight_c_header(model, ps):
    header = 'struct keysight_header_t {\n'
    header += '\tuint32_t switch_id;\n'
    header += '\tuint32_t path_id;\n'
    header += '\tuint8_t table_action_list[%d];\n'%((len(model.get_table_names()) + 1) /2)

    for p in ps:
        tmp = '\t%s'%p[0]
        l = 0
        if len(p) == 1:
            l = 8
        else:
            l = model.get_field_bits(p[0], p[1])
            if l is None:
                print "Error: %s.%s"%(p[0], p[1])
            for x in p[1:]:
                tmp += '_%s' % x
        if l <= 8:
            header += '\tuint8_t %s;\n'%(tmp)
        elif l <= 16:
            header += '\tuint16_t %s;\n'%(tmp)
        elif l <= 32:
            header += '\tuint32_t %s;\n' % (tmp)
        else:
            b = (l + 7)/8
            header += '\tuint8_t %s[%d];\n'%(tmp, b)
    header += '};\n'
    return header


def generate_keysight_c(file_name, model, ps):
    '''
    Generate the C header file containing the postcrad structure
    :param file_name: the destination file name
    :param model: the pipeline model
    :param ps: the postcard structure
    :return:
    '''
    f = open(file_name, 'w')
    f.write('#ifndef KEYSIGHT_HEADER_H\n')
    f.write('#define KEYSIGHT_HEADER_H\n')
    f.write(generate_keysight_c_header(model, ps))
    f.write('\n#endif\n')
    f.close()


def print_statistics_with_format(model, ps):
    dup_count = 0
    dup_size = 0
    size = 32 + len(model.get_table_names())*8
    count = 0
    for p in ps:
        count += 1
        if len(p) == 1:
            continue
        else:
            try :
                size += model.get_field_bits(p[0], p[1])
            except BaseException:
                print p
        if len(p) == 3:
            dup_count += 1
            dup_size += model.get_field_bits(p[0], p[1])

    print '%d\t%d\t%d'%(len(model.get_table_names())*8, size, dup_count)
    return len(model.get_table_names())*8, size, dup_count


def print_statistics(model, ps):
    dup_count = 0
    dup_size = 0
    size = 0
    count = 0

    for p in ps:
        count += 1
        if len(p) == 1:
            size += 8
        else:
            size += model.get_field_bits(p[0], p[1])
        if len(p) == 3:
            dup_count += 1
            dup_size += model.get_field_bits(p[0], p[1])
    print 'Field count: %d'%count
    print 'Field size: %d bits'%size
    print 'Replication field count: %d'%dup_count
    print 'Replication field size: %d bits'%dup_size


def main():
    model = P4Model(sys.argv[1])
    ks, dup, p_dup = field_replication(model)
    ps = ps_merge(model, ks)
    # print ps
    # Generate KeySight Program with P4
    # generate_keysight_p4('keysight.p4', model, ks, dup, p_dup, ps)

    # Generate KeySight Program with C
    # generate_keysight_c('keysight_header.h', model, ps)

    print_statistics_with_format(model, ps)


if __name__ == "__main__":
    main()