import hashlib
from collections import OrderedDict

from .basic_module_generator import ModuleGenerator, get_transition_to_no_infection
from .helpers import load_config, TransitionStates, prob_val, round_val, AttrKeys,\
    get_age_transition, get_uniform_distribution


class AIMedModuleGenerator(ModuleGenerator):
    def generate_module(self, condition, symptoms=None):
        if symptoms is None:
            symptoms = {}
        if not condition.get("symptoms", None):
            return None

        condition_name = condition.get("condition_name")
        condition_slug = condition.get("condition_slug")

        begin_processing_transition = "Begin_Module_Transition"
        potential_infection_transition = "Potential_Infection"
        num_symptom_attribute = "count_symptom_%s" % condition_slug
        history_age_attribute = "age_time_to_the_end"
        node_infection_name = condition_name.replace(" ", "_") + "_Infection"
        target_encounter_start = "Doctor_Visit"
        target_encounter_end = "End_Doctor_Visit"

        no_infection = "No_Infection"

        states = OrderedDict()

        states["Initial"] = {
            "type": "Initial",
            "direct_transition": "Check_History_Age_Attribute"
        }
        states[no_infection] = {
            "type": "Simple",
            "direct_transition": "TerminalState"
        }

        # check if the time history is verified
        states["Check_History_Age_Attribute"] = {
            "type": "Guard",
            "allow": {
                "condition_type": "Attribute",
                "attribute": history_age_attribute,
                "operator": "<=",
                "value": 0
            },
            "direct_transition": begin_processing_transition
        }

        states[begin_processing_transition] = {
            "type": "Simple"
        }

        # sex states
        race_check = "Check_Race"
        sex_conditional_transition, sex_states = self.generate_transition_for_sex(
            condition_name, condition.get("sex"), race_check, TransitionStates.TERMINAL_STATE
        )
        states[begin_processing_transition]["conditional_transition"] = sex_conditional_transition
        states.update(sex_states)

        # add Check_Race node
        states[race_check] = {
            "type": "Simple"
        }

        # race states
        race_conditional_transition, race_states = self.generate_transition_for_race(
            condition_name,
            condition.get("race", {}), potential_infection_transition, no_infection
        )
        states[race_check]["conditional_transition"] = race_conditional_transition
        states.update(race_states)

        age_conditional_transition, age_states = self.generate_transition_for_age(
            condition_name,
            condition.get("age"),
            target_encounter_start,
            potential_infection_transition
        )

        states[potential_infection_transition] = {
            "type": "Simple",
            "conditional_transition": age_conditional_transition
        }
        states.update(age_states)

        states[target_encounter_start] = {
            "type": "Encounter",
            "encounter_class": "ambulatory",
            "reason": "%s_Infection" % condition_name,
            "codes": [
                {
                    "system": "SNOMED-CT",
                    "code": "185345009",
                    "display": "Encounter for symptom"
                }
            ],
            "direct_transition": node_infection_name
        }

        # add the Condition state (a ConditionOnset) stage
        condition_hash = hashlib.sha224(
            condition.get("condition_slug").encode("utf-8")
        ).hexdigest()

        condition_code = {
            "system": "sha224",
            "code": condition_hash,
            "display": condition_name
        }

        next_stage = "Simple_Transition_1"
        if self.config.min_symptoms > 0:
            states["Init_Symptom_Counter"] = {
                "type": "SetAttribute",
                "attribute": num_symptom_attribute,
                "value": 0,
                "direct_transition": next_stage
            }
            next_stage = "Init_Symptom_Counter"

        states[node_infection_name] = {
            "type": "ConditionOnset",
            "codes": [condition_code],
            "target_encounter": "Doctor_Visit",
            "remarks": [
                condition.get("condition_description"),
                condition.get("condition_remarks")
            ],
            "direct_transition": next_stage
        }

        # now we start to model the symptoms, we use
        condition_symptoms = condition.get("symptoms")
        keys = [
            [k, float(condition_symptoms.get(k).get("probability")) * 1 / 100]
            for k in condition_symptoms.keys()
        ]

        for idx, key in enumerate(keys):
            key.append(idx)

        # sort symptoms in the ascending order
        keys = sorted(keys, key=lambda x: x[1])

        if len(keys) > 0:
            if self.config.min_symptoms > 0:
                states["Init_Symptom_Counter"][
                    "direct_transition"] = "Simple_Transition_%d" % (keys[0][2] + 1)
            else:
                states[node_infection_name][
                    "direct_transition"] = "Simple_Transition_%d" % (keys[0][2] + 1)

        for idx in range(len(keys)):
            curr_symptom = condition_symptoms.get(keys[idx][0])
            probability = keys[idx][1]
            index = keys[idx][2]
            slug = curr_symptom.get("slug")
            check_on_num_symptoms = False

            if self.config.min_symptoms > 0:
                remaining = len(keys) - idx
                if remaining <= self.config.min_symptoms:
                    check_on_num_symptoms = True

            symptom_definition = symptoms.get(slug, None)
            if idx == len(keys) - 1:
                next_target = target_encounter_end
            else:
                next_index = keys[idx + 1][2]
                next_target = "Simple_Transition_%d" % (next_index + 1)

            simple_transition_name = "Simple_Transition_%d" % (index + 1)
            symptom_transition_name = "Symptom_%d" % (index + 1)

            next_stage = next_target
            if self.config.min_symptoms > 0:
                inc_symptom = "Inc_Symptom_%d" % (index + 1)
                states[inc_symptom] = {
                    "type": "Counter",
                    "attribute": num_symptom_attribute,
                    "action": "increment",
                    "direct_transition": next_stage
                }
                next_stage = inc_symptom

            next_point = next_target
            if check_on_num_symptoms:
                check_symptom = "Check_Symptom_%d" % (index + 1)
                states[check_symptom] = {
                    "type": "Simple",
                    "conditional_transition": [
                        {
                            "condition": {
                                "condition_type": "Attribute",
                                "attribute": num_symptom_attribute,
                                "operator": "<",
                                "value": self.config.min_symptoms
                            },
                            "transition": symptom_transition_name
                        },
                        {
                            "transition": next_point
                        }
                    ]
                }
                next_point = check_symptom

            if symptom_definition is None:
                # a symptom which we dont have a definition for?
                slug_hash = hashlib.sha224(slug.encode("utf-8")).hexdigest()
                symptom_transition = {
                    "type": "Symptom",
                    "symptom": slug,
                    "range": {
                        "low": 25,
                        "high": 50
                    },
                    "condition_codes": [condition_code],
                    "symptom_code": {
                        "system": "sha224",
                        "code": slug_hash,
                        "display": slug
                    },
                    "value_code": {
                        "system": "sha224",
                        "code": slug,
                        "display": "%s (finding)" % slug
                    },
                    "remarks": [],
                    "direct_transition": next_stage
                }
                simple_transition = {
                    "type": "Simple",
                    "distributed_transition": [
                        {
                            "distribution": probability,
                            "transition": symptom_transition_name
                        },
                        {
                            "distribution": 1 - probability,
                            "transition": next_point
                        }
                    ]
                }
            else:
                symptom_transition = {
                    "type": "Symptom",
                    "symptom": symptom_definition.get("name"),
                    "range": {
                        "low": 25,
                        "high": 50
                    },
                    "condition_codes": [condition_code],
                    "symptom_code": {
                        "system": "sha224",
                        "code": symptom_definition.get("hash"),
                        "display": symptom_definition.get("name")
                    },
                    "value_code": {
                        "system": "sha224",
                        "code": symptom_definition.get("hash"),
                        "display": "%s (finding)" % symptom_definition.get("name")
                    },
                    "remarks": [
                        symptom_definition.get("description")
                    ],
                    "direct_transition": next_stage
                }
                simple_transition = {
                    "type": "Simple",
                    "distributed_transition": [
                        {
                            "distribution": probability,
                            "transition": symptom_transition_name
                        },
                        {
                            "distribution": 1 - probability,
                            "transition": next_point
                        }
                    ]
                }

            states[simple_transition_name] = simple_transition
            states[symptom_transition_name] = symptom_transition

        # always end the encounter
        states[target_encounter_end] = {
            "type": "EncounterEnd",
            "direct_transition": "ConditionEnds"
        }

        states["ConditionEnds"] = {
            "type": "ConditionEnd",
            "direct_transition": "TerminalState",
            "condition_onset": node_infection_name
        }

        states[TransitionStates.TERMINAL_STATE] = {
            "type": "Terminal"
        }

        return {
            "name": condition_name,
            "states": states
        }

    @staticmethod
    def generate_transition_for_age(condition, age_distribution, next_state,
                                    default_state=TransitionStates.TERMINAL_STATE):
        """
        :param condition:
        :param age_distribution:
        :param next_state:
        :param default_state:
        :return:
        """
        transitions = []
        adjacent_states = {}
        # should I include default transition?
        default_flag = False

        if len(age_distribution) == 0:
            age_distribution = get_uniform_distribution(AttrKeys.AGE_KEYS)

        age_keys = list(age_distribution.keys())
        for idx, key in enumerate(age_keys):
            distribution = age_distribution.get(key)
            if "odds" in distribution:
                prob = prob_val(distribution.get("odds"))
            else:
                prob = distribution.get("probability")/100

            if prob <= 0:
                # then terminate module
                continue

            transition = get_age_transition(distribution)
            next_node_name = transition.get("transition")
            state = get_transition_to_no_infection() if prob <= 0 else {
                "type": "Simple",
                "distributed_transition": [
                    {
                        "transition": next_state,
                        "distribution": prob
                    },
                    {
                        "transition": default_state,
                        "distribution": 1 - prob
                    }
                ]
            }
            transitions.append(transition)
            adjacent_states[next_node_name] = state

            if (idx == len(age_keys) - 1) and default_flag:
                transitions.append({
                    "transition": default_state
                })

        return transitions, adjacent_states

    @staticmethod
    def generate_transition_for_sex(condition, sex_distribution, next_state,
                                    default_state=TransitionStates.TERMINAL_STATE):
        if len(sex_distribution) == 0:
            male_prob = female_prob = 0.5
        else:
            male_distribution = sex_distribution.get("sex-male")
            female_distribution = sex_distribution.get("sex-female")
            if "odds" in male_distribution:
                male_prob = prob_val(male_distribution.get("odds"))
                female_prob = prob_val(female_distribution.get("odds"))
            else:
                male_prob = male_distribution.get("probability")/100
                female_prob = female_distribution.get("probability")/100

        probabilities = [male_prob, female_prob]

        transition = []
        adjacent_states = {}
        # should I include default transition?
        default_flag = False
        for idx in range(len(probabilities)):
            if probabilities[idx] > 0:
                next_node_name = "Male" if idx == 0 else "Female"
                transition.append({
                    "condition": {
                        "condition_type": "Gender",
                        "gender": "M" if idx == 0 else "F"
                    },
                    "transition": next_node_name
                })

                state = {
                    "type": "Simple",
                    "distributed_transition": [
                        {
                            "distribution": probabilities[idx],
                            "transition": next_state
                        },
                        {
                            "distribution": 1 - probabilities[idx],
                            "transition": default_state
                        }
                    ],
                    "remarks": [
                        "{} have an approx lifetime risk of {} of {}%.".format(
                            "Men" if idx == 0 else "Women",
                            condition,
                            probabilities[idx] * 100
                        )
                    ]
                }
                adjacent_states[next_node_name] = state
            else:
                default_flag = True

            if (idx == len(probabilities) - 1) and default_flag:
                transition.append({
                    "transition": default_state
                })

        return transition, adjacent_states

    @staticmethod
    def generate_transition_for_race(condition, race_distribution, next_state, default_state=TransitionStates.TERMINAL_STATE):
        transitions = []
        adjacent_states = {}

        if len(race_distribution) == 0:
            race_distribution = get_uniform_distribution(AttrKeys.RACE_KEYS)

        for key in AttrKeys.RACE_KEYS:
            distribution = race_distribution.get(key)
            if "odds" in distribution:
                prob = prob_val(distribution.get("odds"))
            else:
                prob = distribution.get("probability")

            if prob <= 0:
                prob = 0.001

            if key == "race-ethnicity-other":
                # split this into three for : NATIVE, "ASIAN" and "OTHER" according
                # to synthea
                for idx, item in enumerate(["Native", "Asian", "Other"]):
                    next_node_name = "Race_{}".format(item)
                    curr_transition = {
                        "condition": {
                            "condition_type": "Race",
                            "race": item
                        },
                        "transition": next_node_name
                    }
                    transitions.append(curr_transition)

                    state = {
                        "type": "Simple",
                        "distributed_transition": [
                            {
                                "transition": next_state,
                                "distribution": prob
                            },
                            {
                                "transition": default_state,
                                "distribution": 1 - prob
                            }
                        ],
                    }
                    adjacent_states[next_node_name] = state
            else:
                next_node_name = "Race_{}".format(
                    race_distribution.get(key).get("name")
                )
                curr_transition = {
                    "condition": {
                        "condition_type": "Race",
                        "race": race_distribution.get(key).get("name")
                    },
                    "transition": next_node_name
                }
                transitions.append(curr_transition)

                state = {
                    "type": "Simple",
                    "distributed_transition": [
                        {
                            "transition": next_state,
                            "distribution": prob
                        },
                        {
                            "transition": default_state,
                            "distribution": 1 - prob
                        }
                    ]
                }
                adjacent_states[next_node_name] = state

        return transitions, adjacent_states


class AIMedNLICEGenerator(AIMedModuleGenerator):

    def normalise_nlice(self, nlice):
        sum_prob = sum(nlice.values())
        is_100 = True
        if sum_prob < 100:
            is_100 = False
            sum_prob = 100
        keys = list(nlice.keys())
        for key in keys:
            nlice[key] = nlice[key]/sum_prob

        return nlice, is_100

    def get_nlice_states(self, symptom_name, symptom_data, condition_code, next_state):
        if "nlice" not in symptom_data:
            return {}

        nlice_prefix = "Symptom_%s-nlice" % symptom_name
        states = OrderedDict()
        nlice = symptom_data.get("nlice")
        nlice_keys = list(nlice.keys())
        for idx, key in enumerate(nlice_keys):
            if idx == len(nlice_keys) - 1:
                next_node = next_state
            else:
                next_nlice_key = nlice_keys[idx+1]
                next_node = list(nlice.get(next_nlice_key).keys())[0]
                next_node = "transition-%s-%s-%s" % (nlice_prefix, next_nlice_key, next_node)

            # get the normalised nlice values
            normalised_nlice, is_100 = self.normalise_nlice(nlice.get(key))
            normalised_keys = list(normalised_nlice.keys())
            for jdx, attribute in enumerate(normalised_keys):
                nlice_identifier = "%s-nlice-%s-%s" % (symptom_name, key, attribute)
                nlice_state_name = "%s-%s-%s" % (nlice_prefix, key, attribute)
                slug_hash = hashlib.sha224(nlice_identifier.encode("utf-8")).hexdigest()
                probability = normalised_nlice.get(attribute)
                nlice_transition_name = "transition-%s" % nlice_state_name
                nlice_state = {
                    "type": "Symptom",
                    "symptom": nlice_state_name,
                    "range": {
                        "low": 25,
                        "high": 50
                    },
                    "condition_codes": [condition_code],
                    "symptom_code": {
                        "system": "sha224",
                        "code": slug_hash,
                        "display": nlice_identifier
                    },
                    "value_code": {
                        "system": "sha224",
                        "code": nlice_identifier,
                        "display": "%s (finding)" % nlice_identifier
                    },
                    "direct_transition": next_node,
                    "remarks": []
                }

                if jdx == len(normalised_keys) - 1:
                    if is_100:
                        transition_state = {
                            "type": "Simple",
                            "direct_transition": nlice_state_name,
                        }
                    else:
                        transition_state = {
                            "type": "Simple",
                            "distributed_transition": [
                                {
                                    "distribution": probability,
                                    "transition": nlice_state_name
                                },
                                {
                                    "distribution": 1-probability,
                                    "transition": next_node
                                }
                            ]
                        }
                else:
                    next_nlice_state = "%s-%s-%s" % (nlice_prefix, key, normalised_keys[jdx + 1])
                    transition_state = {
                        "type": "Simple",
                        "distributed_transition": [
                            {
                                "distribution": probability,
                                "transition": nlice_state_name
                            },
                            {
                                "distribution": 1 - probability,
                                "transition": "transition-%s" % next_nlice_state
                            }
                        ]
                    }

                states[nlice_transition_name] = transition_state
                states[nlice_state_name] = nlice_state
        return states

    def generate_module(self, condition, symptoms=None):
        if symptoms is None:
            symptoms = {}

        if not condition.get("symptoms", None):
            return None

        condition_name = condition.get("condition_name")
        condition_slug = condition.get("condition_slug")

        begin_processing_transition = "Begin_Module_Transition"
        potential_infection_transition = "Potential_Infection"
        num_symptom_attribute = "count_symptom_%s" % condition_slug
        history_age_attribute = "age_time_to_the_end"
        node_infection_name = condition_name.replace(" ", "_") + "_Infection"
        target_encounter_start = "Doctor_Visit"
        target_encounter_end = "End_Doctor_Visit"

        no_infection = "No_Infection"

        states = OrderedDict()

        states["Initial"] = {
            "type": "Initial",
            "direct_transition": "Check_History_Age_Attribute"
        }
        states[no_infection] = {
            "type": "Simple",
            "direct_transition": "TerminalState"
        }

        # check if the time history is verified
        states["Check_History_Age_Attribute"] = {
            "type": "Guard",
            "allow": {
                "condition_type": "Attribute",
                "attribute": history_age_attribute,
                "operator": "<=",
                "value": 0
            },
            "direct_transition": begin_processing_transition
        }

        states[begin_processing_transition] = {
            "type": "Simple"
        }

        # sex states
        race_check = "Check_Race"
        sex_conditional_transition, sex_states = self.generate_transition_for_sex(
            condition_name, condition.get("sex"), race_check, TransitionStates.TERMINAL_STATE
        )
        states[begin_processing_transition]["conditional_transition"] = sex_conditional_transition
        states.update(sex_states)

        # add Check_Race node
        states[race_check] = {
            "type": "Simple"
        }

        # race states
        race_conditional_transition, race_states = self.generate_transition_for_race(
            condition_name,
            condition.get("race", {}), potential_infection_transition, no_infection
        )
        states[race_check]["conditional_transition"] = race_conditional_transition
        states.update(race_states)

        age_conditional_transition, age_states = self.generate_transition_for_age(
            condition_name,
            condition.get("age"),
            target_encounter_start,
            potential_infection_transition
        )

        states[potential_infection_transition] = {
            "type": "Simple",
            "conditional_transition": age_conditional_transition
        }
        states.update(age_states)

        states[target_encounter_start] = {
            "type": "Encounter",
            "encounter_class": "ambulatory",
            "reason": "%s_Infection" % condition_name,
            "codes": [
                {
                    "system": "SNOMED-CT",
                    "code": "185345009",
                    "display": "Encounter for symptom"
                }
            ],
            "direct_transition": node_infection_name
        }

        # add the Condition state (a ConditionOnset) stage
        condition_hash = hashlib.sha224(
            condition.get("condition_slug").encode("utf-8")
        ).hexdigest()

        condition_code = {
            "system": "sha224",
            "code": condition_hash,
            "display": condition_name
        }

        next_stage = "Simple_Transition_1"
        if self.config.min_symptoms > 0:
            states["Init_Symptom_Counter"] = {
                "type": "SetAttribute",
                "attribute": num_symptom_attribute,
                "value": 0,
                "direct_transition": next_stage
            }
            next_stage = "Init_Symptom_Counter"

        states[node_infection_name] = {
            "type": "ConditionOnset",
            "codes": [condition_code],
            "target_encounter": "Doctor_Visit",
            "remarks": [
                condition.get("condition_description"),
                condition.get("condition_remarks")
            ],
            "direct_transition": next_stage
        }

        # now we start to model the symptoms, we use
        condition_symptoms = condition.get("symptoms")
        keys = [
            [k, float(condition_symptoms.get(k).get("probability")) * 1 / 100]
            for k in condition_symptoms.keys()
        ]

        for idx, key in enumerate(keys):
            key.append(idx)

        # sort symptoms in the ascending order
        keys = sorted(keys, key=lambda x: x[1])

        if len(keys) > 0:
            if self.config.min_symptoms > 0:
                states["Init_Symptom_Counter"][
                    "direct_transition"] = "Simple_Transition_%d" % (keys[0][2] + 1)
            else:
                states[node_infection_name][
                    "direct_transition"] = "Simple_Transition_%d" % (keys[0][2] + 1)

        for idx in range(len(keys)):
            curr_symptom = condition_symptoms.get(keys[idx][0])
            probability = keys[idx][1]
            index = keys[idx][2]
            slug = curr_symptom.get("slug")
            check_on_num_symptoms = False

            if self.config.min_symptoms > 0:
                remaining = len(keys) - idx
                if remaining <= self.config.min_symptoms:
                    check_on_num_symptoms = True

            symptom_definition = symptoms.get(slug, None)
            if idx == len(keys) - 1:
                next_target = target_encounter_end
            else:
                next_index = keys[idx + 1][2]
                next_target = "Simple_Transition_%d" % (next_index + 1)

            simple_transition_name = "Simple_Transition_%d" % (index + 1)
            symptom_transition_name = "Symptom_%d" % (index + 1)

            next_stage = next_target
            if self.config.min_symptoms > 0:
                inc_symptom = "Inc_Symptom_%d" % (index + 1)
                states[inc_symptom] = {
                    "type": "Counter",
                    "attribute": num_symptom_attribute,
                    "action": "increment",
                    "direct_transition": next_stage
                }
                next_stage = inc_symptom

            nlice_states = self.get_nlice_states(slug, curr_symptom, condition_code, next_stage)
            next_point = next_target
            if check_on_num_symptoms:
                check_symptom = "Check_Symptom_%d" % (index + 1)
                states[check_symptom] = {
                    "type": "Simple",
                    "conditional_transition": [
                        {
                            "condition": {
                                "condition_type": "Attribute",
                                "attribute": num_symptom_attribute,
                                "operator": "<",
                                "value": self.config.min_symptoms
                            },
                            "transition": symptom_transition_name
                        },
                        {
                            "transition": next_point
                        }
                    ]
                }
                next_point = check_symptom

            nlice_state_keys = list(nlice_states.keys())
            symptom_target = next_stage if len(nlice_state_keys) == 0 else nlice_state_keys[0]
            if symptom_definition is None:
                # a symptom which we dont have a definition for?
                slug_hash = hashlib.sha224(slug.encode("utf-8")).hexdigest()
                symptom_transition = {
                    "type": "Symptom",
                    "symptom": slug,
                    "range": {
                        "low": 25,
                        "high": 50
                    },
                    "condition_codes": [condition_code],
                    "symptom_code": {
                        "system": "sha224",
                        "code": slug_hash,
                        "display": slug
                    },
                    "value_code": {
                        "system": "sha224",
                        "code": slug,
                        "display": "%s (finding)" % slug
                    },
                    "remarks": [],
                    "direct_transition": symptom_target
                }
                simple_transition = {
                    "type": "Simple",
                    "distributed_transition": [
                        {
                            "distribution": probability,
                            "transition": symptom_transition_name
                        },
                        {
                            "distribution": 1 - probability,
                            "transition": next_point
                        }
                    ]
                }
            else:
                symptom_transition = {
                    "type": "Symptom",
                    "symptom": symptom_definition.get("name"),
                    "range": {
                        "low": 25,
                        "high": 50
                    },
                    "condition_codes": [condition_code],
                    "symptom_code": {
                        "system": "sha224",
                        "code": symptom_definition.get("hash"),
                        "display": symptom_definition.get("name")
                    },
                    "value_code": {
                        "system": "sha224",
                        "code": symptom_definition.get("hash"),
                        "display": "%s (finding)" % symptom_definition.get("name")
                    },
                    "remarks": [
                        symptom_definition.get("description")
                    ],
                    "direct_transition": symptom_target
                }
                simple_transition = {
                    "type": "Simple",
                    "distributed_transition": [
                        {
                            "distribution": probability,
                            "transition": symptom_transition_name
                        },
                        {
                            "distribution": 1 - probability,
                            "transition": next_point
                        }
                    ]
                }

            states[simple_transition_name] = simple_transition
            states[symptom_transition_name] = symptom_transition
            for state_name in nlice_states:
                states[state_name] = nlice_states[state_name]

        # always end the encounter
        states[target_encounter_end] = {
            "type": "EncounterEnd",
            "direct_transition": "ConditionEnds"
        }

        states["ConditionEnds"] = {
            "type": "ConditionEnd",
            "direct_transition": "TerminalState",
            "condition_onset": node_infection_name
        }

        states[TransitionStates.TERMINAL_STATE] = {
            "type": "Terminal"
        }

        return {
            "name": condition_name,
            "states": states
        }
