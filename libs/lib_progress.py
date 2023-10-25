from ase.io.trajectory import Trajectory

import os
import numpy as np
import pandas as pd

from libs.lib_util        import check_mkdir, mpi_print, single_print, generate_msg
from libs.lib_criteria    import get_result, get_criteria
from libs.lib_train       import get_train_job
from libs.lib_termination import get_testerror


def check_progress(inputs, calc_step='cont'):
    """Function [check_progress]
    Check the progress of previous calculations.
    Prepare the recording files if necessary.

    Parameters:

    temperature: float
        The desired temperature in units of Kelvin (K)
    pressure: float
        The desired pressure in units of eV/Angstrom**3

    ntotal: int
        Total number of added training and valdiation data for all subsamplings for each iteractive step
    ntrain: int
        The number of added training data for each iterative step
    nval: int
        The number of added validating data for each iterative step

    nstep: int
        The number of subsampling sets
    nmodel: int
        The number of ensemble model sets with different initialization
    steps_init: int
        Initialize MD steps to get averaged uncertainties and energies
    index: int
        The index of AL interactive step

    crtria: float
        Convergence criteria
    NumAtoms: int
        The number of atoms in the simulation cell
    calc_type: str
        Type of sampling; 'active' (active learning), 'random'

    Returns:

    MD_index: int
        The index for MLMD_main
    index: int
        The index of AL interactive step
    signal: int
        The termination signal
    calc_type: str
        Type of sampling; 'active' (active learning), 'random'
    """

    # Initialization
    MD_index = 0
    MD_step_index = 0
    signal = 0
    
    if inputs.index == 0: # When calculation is just initiated
        # When there is no 'result.txt'
        if not os.path.exists('result.txt'):
            if inputs.rank == 0:
                # Open a recording 'result.txt' file
                outputfile = open('result.txt', 'w')

                result_msg = generate_msg(inputs.al_type)

                outputfile.write(result_msg + '\n')
                outputfile.close()
            # Get the test errors using data-test.npz
            get_testerror(inputs)
        else: # When there is a 'result.txt',
            # Check the contents in 'result.txt' before recording
            if os.path.exists('result.txt'):

                result_msg = generate_msg(inputs.al_type)

                result_data = \
                pd.read_csv('result.txt', index_col=False, delimiter='\t')
                get_criteria_index = len(result_data.loc[:,result_msg[-14:]]);
            else:
                get_criteria_index = -1

            # Print the test errors only for first calculation
            if get_criteria_index == 0:
                # Get the test errors using data-test.npz
                get_testerror(inputs)
    
    # Go through the while loop until a breaking command
    while True:
        # Uncertainty file
        uncert_file = f'UNCERT/uncertainty-{inputs.temperature}K-{inputs.pressure}bar_{inputs.index}.txt'

        # Check the existence of uncertainty file
        if os.path.exists(f'./{uncert_file}'):
            uncert_data = pd.read_csv(uncert_file,\
                                      index_col=False, delimiter='\t')

            if len(uncert_data) == 0: # If it is empty,
                if inputs.rank == 0:
                    check_mkdir('UNCERT')
                    trajfile = open(uncert_file, 'w')
                    trajfile.write(
                        'Temperature[K]\tUncertAbs_E\tUncertRel_E\tUncertAbs_F\tUncertRel_F'
                        +'\tUncertAbs_S\tUncertRel_S\tEpot_average\tS_average'
                        +'\tCounting\tProbability\tAcceptance\n'
                    )
                    trajfile.close()
                break
            else: # If it is not empty,
                # Check the last entry in the 'Couting' column
                uncert_check = np.array(uncert_data.loc[:,'Counting'])[-1]
                MD_step_index = len(uncert_data.loc[:, 'Counting'])
                del uncert_data

                # If it reaches total number of the sampling data
                if uncert_check >= inputs.ntotal and (MD_step_index >= inputs.nperiod if inputs.calc_type == 'period' or calc_step == 'gen' else True):

                    if os.path.exists('result.txt'):
                        result_msg = generate_msg(inputs.al_type)

                        result_data = \
                        pd.read_csv('result.txt', index_col=False, delimiter='\t')
                        get_criteria_index = result_data.loc[:,result_msg[-14:]].isnull().values.any();

                    # Print the test errors
                    if get_criteria_index:
                        # Get the test errors using data-test.npz
                        get_result(inputs)
                    
                    # Check the FHI-vibes calculations
                    aims_check = ['Have a nice day.' in open(f'CALC/{inputs.temperature}K-{inputs.pressure}bar_{inputs.index+1}/{jndex}/aims/calculations/aims.out').read()\
                                   if os.path.exists(f'CALC/{inputs.temperature}K-{inputs.pressure}bar_{inputs.index+1}/{jndex}/aims/calculations/aims.out') else False for jndex in range(inputs.ntotal)];
                    
                    if all(aims_check) == True: # If all FHI-vibes calcs are finished,
                        gen_check = [
                        os.path.exists(f'MODEL/{inputs.temperature}K-{inputs.pressure}bar_{inputs.index+1}/deployed-model_{index_nmodel}_{index_nstep}.pth')
                        for index_nmodel in range(inputs.nmodel) for index_nstep in range(inputs.nstep)
                        ]
                        MD_step_index = 0

                        if all(gen_check) == True:
                            # Get the test errors using data-test.npz
                            inputs.index += 1
                            get_testerror(inputs)
                        else:
                            inputs.index += 1
                            break

                    else: # Not finished
                        single_print(f'DFT calculations of {inputs.temperature}K-{inputs.pressure}bar_{inputs.index+1} are not finished or not started.')
                        signal = 1
                        break
                else: # Otherwise, get the index of MLMD_main
                    MD_index = int(uncert_check)
                    break
        else: # If there is no uncertainty file, create it
            if inputs.rank == 0:
                check_mkdir('UNCERT')
                trajfile = open(uncert_file, 'w')
                trajfile.write(
                    'Temperature[K]\tUncertAbs_E\tUncertRel_E\tUncertAbs_F\tUncertRel_F'
                    +'\tUncertAbs_S\tUncertRel_S\tEpot_average\tS_average'
                    +'\tCounting\tProbability\tAcceptance\n'
                )
                trajfile.close()
            break
            
    return MD_index, MD_step_index, inputs.index, signal


def check_progress_rand(inputs, calc_step='cont'):
    """Function [check_progress_rand]
    Check the progress of previous calculations.
    Prepare the recording files if necessary.

    Parameters:

    temperature: float
        The desired temperature in units of Kelvin (K)
    pressure: float
        The desired pressure in units of eV/Angstrom**3

    ntotal: int
        Total number of added training and valdiation data for all subsamplings for each iteractive step
    ntrain: int
        The number of added training data for each iterative step
    nval: int
        The number of added validating data for each iterative step

    nstep: int
        The number of subsampling sets
    nmodel: int
        The number of ensemble model sets with different initialization
    steps_init: int
        Initialize MD steps to get averaged uncertainties and energies
    index: int
        The index of AL interactive step

    crtria: float
        Convergence criteria
    NumAtoms: int
        The number of atoms in the simulation cell
    calc_type: str
        Type of sampling; 'active' (active learning), 'random'

    Returns:

    MD_index: int
        The index for MLMD_main
    index: int
        The index of AL interactive step
    signal: int
        The termination signal
    calc_type: str
        Type of sampling; 'active' (active learning), 'random'
    """

    # Initialization
    MD_index = 0
    signal = 0

    if calc_step == 'gen':
        inputs.index += 1
    
    if inputs.index == 0: # When calculation is just initiated
        # When there is no 'result.txt'
        if not os.path.exists('result.txt'):
            if inputs.rank == 0:
                # Open a recording 'result.txt' file
                outputfile = open('result.txt', 'w')
                outputfile.write(
                    'Temperature[K]\tIteration\t'
                    + 'TestError_E\tTestError_F\tTestError_S\n'
                )
                outputfile.close()
            # Get the test errors using data-test.npz
            get_testerror(inputs)
        else: # When there is a 'result.txt',
            # Check the contents in 'result.txt' before recording
            if os.path.exists('result.txt'):
                result_data = \
                pd.read_csv('result.txt', index_col=False, delimiter='\t')
                inputs.index = len(result_data.loc[:,'Iteration']);
            else:
                inputs.index = -1

            # Print the test errors only for first calculation
            # if index == 0:
            # Get the test errors using data-test.npz
            get_testerror(inputs)

    # Go through the while loop until a breaking command
    while True:
        # Check the FHI-vibes calculations
        aims_check = ['Have a nice day.' in open(f'CALC/{inputs.temperature}K-{inputs.pressure}bar_{inputs.index+1}/{jndex}/aims/calculations/aims.out').read()\
                       if os.path.exists(f'CALC/{inputs.temperature}K-{inputs.pressure}bar_{inputs.index+1}/{jndex}/aims/calculations/aims.out') else False for jndex in range(inputs.ntotal)];
        
        if all(aims_check) == True: # If all FHI-vibes calcs are finished,
            gen_check = [
            os.path.exists(f'MODEL/{inputs.temperature}K-{inputs.pressure}bar_{inputs.index+1}/deployed-model_{index_nmodel}_{index_nstep}.pth')
            for index_nmodel in range(inputs.nmodel) for index_nstep in range(inputs.nstep)
            ]

            if all(gen_check) == True:
                # Get the test errors using data-test.npz
                inputs.index += 1
                get_testerror(inputs)
            else:
                inputs.index += 1
                break
        else:
            break
    return MD_index, inputs.index, signal



def check_index():
    """Function [check_index]
    Check the progress of previous calculations
    and return the index of AL interactive steps.

    Parameters:

    index: int
        The index of AL interactive steps

    Returns:

    index: int
        The index of AL interactive steps from recorded file
    """

    # Open 'result.txt'
    if os.path.exists('result.txt'):
        result_data = \
        pd.read_csv('result.txt', index_col=False, delimiter='\t')
        result_index = len(result_data.loc[:,'Iteration']); del result_data
        if result_index > 0:
            total_index = result_index - 1
        else:
            total_index = result_index
    else:
        total_index = 0
  
    return total_index
    