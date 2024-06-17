from ase.io.trajectory import Trajectory
from ase.io.trajectory import TrajectoryWriter
import ase.units as units
from ase.io.cif        import write_cif

import time
import os
import random
import numpy as np
import pandas as pd
from decimal import Decimal
from ase.build import make_supercell
from ase.io import read as atoms_read
from ase.md.velocitydistribution import MaxwellBoltzmannDistribution

from libs.lib_util    import single_print
from libs.lib_MD_util import get_forces, get_MDinfo_temp, get_masses
from libs.lib_criteria import eval_uncert, uncert_strconvter, get_criteria, get_criteria_prob

import torch
torch.set_default_dtype(torch.float64)

def cont_NVTLangevin_temp(
    inputs, struc, timestep, temperature, calculator, E_ref,
    MD_index, MD_step_index, signal_uncert=False, signal_append=True, fix_com=True,
):
    """Function [NVTLangevin]
    Evalulate the absolute and relative uncertainties of
    predicted energies and forces.
    This script is adopted from ASE Langevin function 
    and modified to use averaged forces from trained model.

    Parameters:

    struc: ASE atoms
        A structral configuration of a starting point
    timestep: float
        The step interval for printing MD steps
    temperature: float
        The desired temperature in units of Kelvin (K)

    friction: float
        Strength of the friction parameter in NVTLangevin ensemble
    steps: int
        The length of the Molecular Dynamics steps
    loginterval: int
        The step interval for printing MD steps

    nstep: int
        The number of subsampling sets
    nmodel: int
        The number of ensemble model sets with different initialization
    calculator: ASE calculator
        Any calculator
    E_ref: flaot
        The energy of reference state (Here, ground state)
    al_type: str
        Type of active learning: 'energy', 'force', 'force_max'
    trajectory: str
        A name of MD trajectory file

    logfile: str (optional)
        A name of MD logfile. With None, it will not print a log file.
    signal_uncert: bool (optional)


    fixcm: bool (optional)
        If True, the position and momentum of the center of mass is
        kept unperturbed.  Default: True.
    """

    time_init = time.time()

    # Initialization of index
    condition = f'{inputs.temperature}K-{inputs.pressure}bar'

    trajectory = f'TEMPORARY/temp-{condition}_{inputs.index}.traj'
    logfile = f'TEMPORARY/temp-{condition}_{inputs.index}.log'

    # Extract the criteria information from the initialization step
    criteria_collected = get_criteria(inputs.temperature, inputs.pressure, inputs.index, inputs.steps_init, inputs.al_type)

    if os.path.exists(trajectory):
        traj_temp = Trajectory(trajectory)
        struc = traj_temp[-1]
        MD_step_index = len(traj_temp)
        del traj_temp

    # mpi_print(f'Step 1: {time.time()-time_init}', rank)
    if MD_step_index == 0: # If appending and the file exists,
        file_traj = TrajectoryWriter(filename=trajectory, mode='w')
        # Add new configuration to the trajectory file
        file_traj.write(atoms=struc)
            
        if isinstance(logfile, str):
            file_log = open(logfile, 'w')
            file_log.write(
                'Time[ps]   \tEtot[eV]   \tEpot[eV]    \tEkin[eV]   \t'
                + 'Temperature[K]'
                )
            if signal_uncert:
                file_log.write(
                    '\tUncertAbs_E\tUncertRel_E\t'
                    + 'UncertAbs_F\tUncertRel_F\t'
                    + 'UncertAbs_S\tUncertRel_S\tS_average\n'
                    )
            else:
                file_log.write('\n')
            file_log.close()
        
            # Get MD information at the current step
            info_TE, info_PE, info_KE, info_T = get_MDinfo_temp(
                struc, inputs.nstep, inputs.nmodel, calculator, inputs.harmonic_F, E_ref
                )

            if signal_uncert:
                # Get absolute and relative uncertainties of energy and force
                # and also total energy
                uncerts, Epot_step, S_step =\
                eval_uncert(struc, inputs.nstep, inputs.nmodel, E_ref, calculator, inputs.al_type, inputs.harmonic_F)

            # Log MD information at the current step in the log file
            file_log = open(logfile, 'a')
            file_log.write(
                '{:.5f}'.format(Decimal(str(0.0))) + '   \t' +
                '{:.5e}'.format(Decimal(str(info_TE))) + '\t' +
                '{:.5e}'.format(Decimal(str(info_PE))) + '\t' +
                '{:.5e}'.format(Decimal(str(info_KE))) + '\t' +
                '{:.2f}'.format(Decimal(str(info_T)))
                )
            if signal_uncert:
                file_log.write(
                    '      \t' +
                    uncert_strconvter(uncerts.UncertAbs_E) + '\t' +
                    uncert_strconvter(uncerts.UncertRel_E) + '\t' +
                    uncert_strconvter(uncerts.UncertAbs_F) + '\t' +
                    uncert_strconvter(uncerts.UncertRel_F) + '\t' +
                    uncert_strconvter(uncerts.UncertAbs_S) + '\t' +
                    uncert_strconvter(uncerts.UncertRel_S) + '\t' +
                    uncert_strconvter(S_step) + '\n'
                    )
            else:
                file_log.write('\n')
            file_log.close()
    else:
        file_traj = TrajectoryWriter(filename=trajectory, mode='a')

    write_traj = TrajectoryWriter(
        filename=f'TRAJ/traj-{condition}_{inputs.index+1}.traj',
        mode='a'
        )

    # Get averaged force from trained models
    forces, step_std = get_forces_temp(struc, inputs.nstep, inputs.nmodel, calculator, inputs.harmonic_F, inputs.anharmonic_F, criteria_collected, inputs.al_type, E_ref)

    # mpi_print(f'Step 3: {time.time()-time_init}', rank)
    # Go trough steps until the requested number of steps
    # If appending, it starts from Langevin_idx. Otherwise, Langevin_idx = 0
    while (MD_index < inputs.ntotal) or (inputs.calc_type == 'period' and MD_step_index < inputs.nperiod*inputs.loginterval):

        accept = '--         '
        natoms = len(struc)

        # mpi_print(f'Step 6: {time.time()-time_init}', rank)
        # Get averaged forces and velocities
        if forces is None:
            forces, step_std = get_forces_temp(struc, inputs.nstep, inputs.nmodel, calculator, inputs.harmonic_F, inputs.anharmonic_F, criteria_collected, inputs.al_type, E_ref)
        # Velocity is already calculated based on averaged forces
        # in the previous step
        velocity = struc.get_velocities()
        
        # mpi_print(f'Step 7: {time.time()-time_init}', rank)
        # Sample the random numbers for the temperature fluctuation
        # xi = np.empty(shape=(natoms, 3))
        # eta = np.empty(shape=(natoms, 3))
        xi = np.random.standard_normal(size=(natoms, 3))
        eta = np.random.standard_normal(size=(natoms, 3))

        # mpi_print(f'Step 4: {time.time()-time_init}', rank)
        # Get essential properties
        masses = get_masses(struc.get_masses(), natoms)

        # mpi_print(f'Step 5: {time.time()-time_init}', rank)
        # Get Langevin coefficients
        sigma = np.sqrt(2 * temperature * inputs.friction / masses)
        c1 = timestep / 2. - timestep * timestep * inputs.friction / 8.
        c2 = timestep * inputs.friction / 2 - timestep * timestep * inputs.friction * inputs.friction / 8.
        c3 = np.sqrt(timestep) * sigma / 2. - timestep**1.5 * inputs.friction * sigma / 8.
        c5 = timestep**1.5 * sigma / (2 * np.sqrt(3))
        c4 = inputs.friction / 2. * c5

        if inputs.al_type == 'force_max':
            uncert_ratio = (step_std[inputs.idx_atom] - criteria_collected.Un_Abs_F_avg_i) / (criteria_collected.Un_Abs_F_std_i)
            if step_std[inputs.idx_atom] - criteria_collected.Un_Abs_F_avg_i < 0:
                temp_ratio = 1.0
            else:
                temp_ratio = np.exp( (-1/2) * (uncert_ratio)**2 )
        elif inputs.al_type == 'energy_max':
            uncert_ratio = (step_std[inputs.idx_atom] - criteria_collected.Un_Abs_E_avg_i) / (criteria_collected.Un_Abs_E_std_i)
            if step_std[inputs.idx_atom] - criteria_collected.Un_Abs_E_avg_i < 0:
                temp_ratio = 1.0
            else:
                temp_ratio = np.exp( (-1/2) * (uncert_ratio)**2 )

        single_print(f'Step {MD_step_index}; temp activate (atom {inputs.idx_atom}):{temp_ratio}')
        sigma_elem = np.sqrt(2 * (temperature + temp_ratio * (inputs.temp_factor * units.kB)) * inputs.friction / masses[inputs.idx_atom][0])
        c3_elem = np.sqrt(timestep) * sigma_elem / 2. - timestep**1.5 * inputs.friction * sigma_elem / 8.
        c5_elem = timestep**1.5 * sigma_elem / (2 * np.sqrt(3))
        c4_elem = inputs.friction / 2. * c5_elem

        c3[inputs.idx_atom] = c3_elem
        c4[inputs.idx_atom] = c4_elem
        c5[inputs.idx_atom] = c5_elem

        # c3, c4, c5 = [], [], []

        # for idx_atom, (F_std, mass) in enumerate(zip(F_step_norm_std, masses)):
        #     uncert_ratio = (F_std - criteria_collected.Un_Abs_F_avg_i*0.9) / (criteria_collected.Un_Abs_F_std_i * 0.1)
        #     temp_ratio = 1.0 if uncert_ratio < 0 else 100.0 if uncert_ratio > 99 else uncert_ratio + 1
        #     if temp_ratio > 1.0:
        #         print(f'Step {MD_step_index}; temp activate (atom {idx_atom}):{temp_ratio}')
        #     sigma_elem = np.sqrt(2 * (temperature * temp_ratio) * inputs.friction / mass[0])
        #     c3_elem = np.sqrt(timestep) * sigma_elem / 2. - timestep**1.5 * inputs.friction * sigma_elem / 8.
        #     c5_elem = timestep**1.5 * sigma_elem / (2 * np.sqrt(3))
        #     c4_elem = inputs.friction / 2. * c5_elem

        #     c3.append(c3_elem)
        #     c4.append(c4_elem)
        #     c5.append(c5_elem)

        # c3 = np.array(c3)[:, np.newaxis]
        # c4 = np.array(c4)[:, np.newaxis]
        # c5 = np.array(c5)[:, np.newaxis]

        # mpi_print(f'Step 8: {time.time()-time_init}', rank)
        # Get get changes of positions and velocities
        rnd_pos = c5 * eta
        rnd_vel = c3 * xi - c4 * eta
        
        # Check the center of mass
        if fix_com:
            rnd_pos -= rnd_pos.sum(axis=0) / natoms
            rnd_vel -= (rnd_vel * masses).sum(axis=0) / (masses * natoms)
            
        # First halfstep in the velocity.
        velocity += (c1 * forces / masses - c2 * velocity + rnd_vel)
        
        # mpi_print(f'Step 9: {time.time()-time_init}', rank)
        # Full step in positions
        position = struc.get_positions()
        
        # Step: x^n -> x^(n+1) - this applies constraints if any.
        struc.set_positions(position + timestep * velocity + rnd_pos)

        # mpi_print(f'Step 10: {time.time()-time_init}', rank)
        # recalc velocities after RATTLE constraints are applied
        velocity = (struc.get_positions() - position - rnd_pos) / timestep
        # mpi_print(f'Step 10-1: {time.time()-time_init}', rank)
        forces, step_std = get_forces_temp(struc, inputs.nstep, inputs.nmodel, calculator, inputs.harmonic_F, inputs.anharmonic_F, criteria_collected, inputs.al_type, E_ref)
        
        # mpi_print(f'Step 10-2: {time.time()-time_init}', rank)
        # Update the velocities
        velocity += (c1 * forces / masses - c2 * velocity + rnd_vel)

        # mpi_print(f'Step 10-3: {time.time()-time_init}', rank)
        # Second part of RATTLE taken care of here
        struc.set_momenta(velocity * masses)
        
        # mpi_print(f'Step 11: {time.time()-time_init}', rank)
        # Log MD information at regular intervals
        if (MD_step_index+1) % inputs.loginterval == 0:

            # Get absolute and relative uncertainties of energy and force
            # and also total energy
            uncerts, Epot_step, S_step =\
            eval_uncert(struc, inputs.nstep, inputs.nmodel, E_ref, calculator, inputs.al_type, inputs.harmonic_F)

            # Get a criteria probability from uncertainty and energy informations
            criteria = get_criteria_prob(inputs, Epot_step, uncerts, criteria_collected)

            # Acceptance check with criteria
            ##!! Epot_step should be rechecked.
            if random.random() < criteria: # and Epot_step > 0.1:
                accept = 'Accepted'
                MD_index += 1
                write_traj.write(atoms=struc)
            else:
                accept = 'Vetoed'

            # Record the MD results at the current step
            trajfile = open(f'UNCERT/uncertainty-{condition}_{inputs.index}.txt', 'a')
            trajfile.write(
                '{:.5e}'.format(Decimal(str(struc.get_temperature()))) + '\t' +
                uncert_strconvter(uncerts.UncertAbs_E) + '\t' +
                uncert_strconvter(uncerts.UncertRel_E) + '\t' +
                uncert_strconvter(uncerts.UncertAbs_F) + '\t' +
                uncert_strconvter(uncerts.UncertRel_F) + '\t' +
                uncert_strconvter(uncerts.UncertAbs_S) + '\t' +
                uncert_strconvter(uncerts.UncertRel_S) + '\t' +
                uncert_strconvter(Epot_step) + '\t' +
                uncert_strconvter(S_step) + '\t' +
                str(MD_index) + '          \t' +
                '{:.5e}'.format(Decimal(str(criteria))) + '\t' +
                str(accept) + '   \n'
            )
            trajfile.close()

            if isinstance(logfile, str):
                # mpi_print(f'Step 12: {time.time()-time_init}', rank)
                info_TE, info_PE, info_KE, info_T = get_MDinfo_temp(
                    struc, inputs.nstep, inputs.nmodel, calculator, inputs.harmonic_F, E_ref
                    )

                # mpi_print(f'Step 14: {time.time()-time_init}', rank)
                file_log = open(logfile, 'a')
                simtime = timestep*(MD_step_index+inputs.loginterval)/units.fs/1000
                file_log.write(
                    '{:.5f}'.format(Decimal(str(simtime))) + '   \t' +
                    '{:.5e}'.format(Decimal(str(info_TE))) + '\t' +
                    '{:.5e}'.format(Decimal(str(info_PE))) + '\t' +
                    '{:.5e}'.format(Decimal(str(info_KE))) + '\t' +
                    '{:.2f}'.format(Decimal(str(info_T)))
                    )
                if signal_uncert:
                    file_log.write(
                        '      \t' +
                        uncert_strconvter(uncerts.UncertAbs_E) + '\t' +
                        uncert_strconvter(uncerts.UncertRel_E) + '\t' +
                        uncert_strconvter(uncerts.UncertAbs_F) + '\t' +
                        uncert_strconvter(uncerts.UncertRel_F) + '\t' +
                        uncert_strconvter(uncerts.UncertAbs_S) + '\t' +
                        uncert_strconvter(uncerts.UncertRel_S) + '\t' +
                        uncert_strconvter(S_step) + '\n'
                        )
                else:
                    file_log.write('\n')
                file_log.close()
                # mpi_print(f'Step 15: {time.time()-time_init}', rank)
            file_traj.write(atoms=struc)

        MD_step_index += 1
        # mpi_print(f'Step 16: {time.time()-time_init}', rank)


def get_forces_temp(
    struc, nstep, nmodel, calculator, harmonic_F, anharmonic_F, criteria, al_type, E_ref
):
    """Function [get_forces]
    Evalulate the average of forces from all different trained models.

    Parameters:

    struc_step: ASE atoms
        A structral configuration at the current step
    nstep: int
        The number of subsampling sets
    nmodel: int
        The number of ensemble model sets with different initialization
    calculator: ASE calculator or list of ASE calculators
        Calculators from trained models

    Returns:

    force_avg: np.array of float
        Averaged forces across trained models
    """

    # time_init = time.time()
    from libs.lib_util import eval_sigma

    # mpi_print(f'Step 10-a: {time.time()-time_init}', rank)
    if type(calculator) == list:
        energy = []
        forces = []
        sigmas = []
        zndex = 0
        for index_nmodel in range(nmodel):
            for index_nstep in range(nstep):
                # mpi_print(f'Step 10-a1 first {rank}: {time.time()-time_init}', rank)
                struc.calc = calculator[zndex]
                # mpi_print(f'Step 10-a1 second {rank}: {time.time()-time_init}', rank)
                temp_force = struc.get_forces()
                # mpi_print(f'Step 10-a1 third {rank}: {time.time()-time_init}', rank)
                if al_type == 'energy_max':
                    energy.append(struc.get_potential_energies() - E_ref[1][zndex])
                else:
                    energy.append(struc.get_potential_energy() - E_ref[0][zndex])
                forces.append(temp_force)
                # sigmas.append(eval_sigma(temp_force, struc.get_positions(), 'force_max'))
                # mpi_print(f'Step 10-a1 last {rank}: {time.time()-time_init}', rank)
                zndex += 1
        # mpi_print(f'Step 10-a2: {time.time()-time_init}', rank)
        # sigmas = comm.allgather(sigmas)
        # mpi_print(f'Step 10-a3: {time.time()-time_init}', rank)
        E_step_avg = np.average(energy, axis=0)
        E_step_std = np.std(energy, axis=0)

        F_step_avg = np.average(forces, axis=0)
        F_step_norm = np.array([[np.linalg.norm(Fcomp) for Fcomp in Ftems] for Ftems in forces - F_step_avg])
        F_step_norm_std = np.sqrt(np.average(F_step_norm ** 2, axis=0))
        F_step_norm_avg = np.linalg.norm(F_step_avg, axis=1)

        # uncert_idcs = np.where((F_step_norm_std - criteria.Un_Abs_F_avg_i) > criteria.Un_Abs_F_std_i * 0.5)[0]
        # print(f'uncert_idcs:{uncert_idcs}')

        force_avg = F_step_avg

    else:
        struc.calc = calculator
        force_avg = struc.get_forces(md=True)

    # mpi_print(f'Step 10-d: {time.time()-time_init}', rank)

    if al_type == 'energy_max':
        return force_avg, E_step_std
    else:
        return force_avg, F_step_norm_std
