## Brian Blaylock
## March 25, 2019

"""
Fractions Skill Score

 Roberts, N.M. and H.W. Lean, 2008: Scale-Selective Verification of Rainfall
    Accumulations from High-Resolution Forecasts of Convective Events. Mon. 
    Wea. Rev., 136, 78–97, https://doi.org/10.1175/2007MWR2123.1

NOTE: This calculation of the Fractions Skill Score is based on the function in
BB_wx_calcs.binary_events, but this is customized for input with multiple
forecast grids and for different domains.
"""

import numpy as np
import scipy.ndimage as ndimage
import multiprocessing
import os
from datetime import datetime, timedelta

def fraction(values):
    '''
    For each set of binary values for the window, compute the fraction of the
    window that is True.
    '''
    return np.sum(values)/np.size(values)

def radial_footprint(radius):
    """A footprint with the given radius"""
    y,x = np.ogrid[-radius: radius+1, -radius: radius+1]
    footprint = x**2+y**2 <= radius**2
    footprint = 1*footprint.astype(float)
    return footprint

def FSS_MP(inputs):
    """
    FSS Multiprocessing for each of the forecast fields
    Inputs: 
        n             - number of job
        fxx_b         - forecast binary grid for the forecast hour
        neighbor_type - ['window' or 'radius']
        w_or_r        - window or radius size
    """
    n, fxx_b, neighbor_type, w_or_r = inputs
    
    print('Working on job %02d: %s = %s grid spaces' % (n, neighbor_type, w_or_r))
    if neighbor_type == 'window':    
        fxx_f = ndimage.generic_filter(fxx_b, fraction, mode='constant', cval=0, size=w_or_r)
    elif neighbor_type == 'radius':
        fxx_f = ndimage.generic_filter(fxx_b, fraction, mode='constant', cval=0, footprint=radial_footprint(w_or_r))
    print('Finished on job %02d: %s = %s grid spaces' % (n, neighbor_type, w_or_r))
    return fxx_f


def fractions_skill_score_SPECIAL(obs_binary, fxx_binary, domains,
                                  window=None, radius=None):
    """
    Fractions Skill Score **FOR MULTIPLE FORECAST GRIDS**

    Input:
        obs_binary - Observed Binary Field (True/False)
        fxx_binary - List of Forecasted Binary Field (True/False) for each 
                     forecast lead time.
        domains    - a domain dictionary returned by 
                     BB_HRRR.HRRR_paths.get_domains(). We need the 'mask' key
                     from each domain.
        window     - Square box window with size as number of grid points. 
                     Preferably an odd number so that the window is equal in
                     all directions. (Used if radius==None)
        radius     - Radius of the footprint. (Used if window==None)
    """
   
    assert np.size(obs_binary) == np.size(fxx_binary[0]), ('Observed Binary and Forecasted Binary input must be same size.')
    assert np.logical_or(window != None, radius != None), ('"window" or "radius" must be specified, but not both.')
    assert np.logical_or(window == None, radius == None), ('"window" or "radius" must be specified, but not both.')
    
    ## a. Convert to binary fields: Convert input from boolean arrays to floats
    #     so we can compute fraction values.
    print('Convert Boolean field to float (1=True, 0=False)')
    obs_binary = np.array(obs_binary, dtype=float)
    fxx_binary = np.array(fxx_binary, dtype=float)

    ## b. Generate fractions: Compute the fractions of the area
    #     "These quantities assess the spatial density in the binary fields."
    #     "Points outside the domain are assigned a value of zero."
    #                                                     - Roberts et al. 2008
    return_this = {}

    # Two different methods: A "window" box or a radial footprint as the filter.
    print('Generate fractions for the neighborhood')
    if window != None:
        print('Window size: %sx%s grid boxes' % (window, window))
        return_this['window'] = window
        
        # Observations fractions
        obs_fracs = ndimage.generic_filter(obs_binary, fraction, size=window, mode='constant', cval=0)
        
        # Use Multiprocessing to compute fractions for each forecast grid.
        # These are the inputs for that function...
        fxx_list = [[n, fxx_b, 'window', window] for n, fxx_b in enumerate(fxx_binary)]
           
    elif radius != None:
        # "It might be preferable to use a different kernel, such as a circular mean filter..."
        print('Footprint radius: %s grid boxes' % radius)
        return_this['radius'] = radius
        
        #Observations fractions
        obs_fracs = ndimage.generic_filter(obs_binary, fraction, footprint=radial_footprint(radius), mode='constant', cval=0)
        
        # Use Multiprocessing to compute fractions for each forecast grid.
        # These are the inputs for that function...
        fxx_list = [[n, fxx_b, 'radius', radius] for n, fxx_b in enumerate(fxx_binary)]
        
    # Multiprocessing function for each forecast hour
        
    if radius != None and radius > 50:
        ## Probably won't have enough memory to handle large neighborhood, so 
        ## If radius > 50, then scale the number of processors based on the 
        ## machine's available memory allocating each process 5 gb of memory.
        import psutil
        memory_gb = (psutil.virtual_memory().available/1e9-2)
        cores = int(memory_gb/5)
        print('Memory Limited: Only use %s cpus' % cores)
        with multiprocessing.Pool(cores) as p:
            fxx_fracs = np.array(p.map(FSS_MP, fxx_list))
            p.close()
            p.join()
    else:
        # Use up to 18 cores
        cores = np.minimum(len(fxx_binary), multiprocessing.cpu_count()-2)
        with multiprocessing.Pool(cores) as p:
            fxx_fracs = np.array(p.map(FSS_MP, fxx_list))
            p.close()
            p.join()

    # We want to return these values for later use if needed
    return_this['Observed Fraction'] = obs_fracs
    return_this['Forecast Fraction'] = fxx_fracs

    ## c. Compute fractions skill score for each domain at all fxx grids.
    # Don't recompute the filter for each domain, just apply the domain mask.
    for DOMAIN in domains:
        return_this[DOMAIN] = {}
        
        mask = domains[DOMAIN]['mask']
        masked_obs_fracs = np.ma.array(obs_fracs, mask=mask)
        masked_fxx_fracs = np.ma.array([np.ma.array(f, mask=mask) for f in fxx_fracs])
        
        print('Compute fractions skill score for %s' % DOMAIN)
        MSE = np.mean((masked_obs_fracs - masked_fxx_fracs)**2, axis=(1,2))
        MSE_ref = np.mean(masked_obs_fracs**2) + np.mean(masked_fxx_fracs**2, axis=(1,2))

        FSS = 1 - (MSE/MSE_ref)

        return_this[DOMAIN] = np.array(FSS)

    return return_this


def write_table_to_file(FSS_dict, DATE, write_domains, fxx=range(1,19), SAVEDIR='./HRRR_GLM_Fractions_Skill_Score/'):
    """
    Inputs:
        FSS - the dictionary returned from
              fractions_skill_score_SPECIAL()
    """
    for DOMAIN in domains:
        if DOMAIN in write_domains:
            # Directories for each Domain
            DOM_DIR = "%s/%s/" % (SAVEDIR, DOMAIN)
            if not os.path.exists(DOM_DIR):
                os.makedirs(DOM_DIR)
            #
            SAVEFILE = "%s/%s_%s.csv" % (DOM_DIR, DOMAIN, DATE.strftime('%Y_m%m_h%H')) 
            #
            # Initiate new file with header if the day of the month is 1.
            if DATE.day == 1:
                FSS_str = ','.join(['F%02d_FSS' % i for i in fxx])
                HEADER = 'DATE,' + FSS_str
                with open(SAVEFILE, "w") as f:
                    f.write('%s\n' % HEADER)
            #
            if FSS_dict is None:
                FSS_str = ','.join(np.array(np.ones_like(fxx)*np.nan, dtype=str))
                line = "%s,%s" % (DATE, FSS_str)
            else:
                FSS_str = ','.join(np.array(np.round(FSS_dict[DOMAIN], 4), dtype=str))
                line = "%s,%s" % (DATE, FSS_str)
            with open(SAVEFILE, "a") as f:
                f.write('%s\n' % line)
            print('Wrote to', SAVEFILE)


def write_to_files_MP(inputs):
    """
    Each iteration will work on a different set of days for the specified
    hour and month, i.e. all 1200 UTC validDates for all days in February.
    """
    year, month, hour, radii = inputs

    sDATE = datetime(year, month, 1, hour)
    if month==12:
        eDATE = datetime(year+1, 1, 1, hour)
    else:
        eDATE = datetime(year, month+1, 1, hour)

    #
    print('\n')
    print('=========================================================')
    print('=========================================================')
    print('       WORKING ON MONTH %s and HOUR %s' % (month, hour))
    print('=========================================================')
    print('=========================================================')
    #
    ### Check if the file we are working on exists
    #
    SAVEDIR = './HRRR_GLM_Fractions_Skill_Score_r%02d/' % radii[0]

    DOMAINS = ['Utah', 'Colorado', 'Texas', 'Florida', 'HRRR', 'West', 'Central', 'East']
    FILES = ["%s/%s/%s_%s.csv" % (SAVEDIR, D, D, sDATE.strftime('%Y_m%m_h%H')) for D in DOMAINS]
    EXISTS = [os.path.exists(i) for i in FILES]

    Next_DATE = []
    for (F, E) in zip(FILES, EXISTS):
        if E:
            list_DATES = np.genfromtxt(F, delimiter=',', names=True, encoding='UTF-8', dtype=None)['DATE']
            if np.shape(list_DATES) == ():
                # I suppose there is only one date in the list
                last = str(list_DATES)
            else:
                # Else, get the last date in the list
                last = list_DATES[-1]
            Next_DATE.append(datetime.strptime(last, '%Y-%m-%d %H:%M:%S')+timedelta(days=1))
        else:
            Next_DATE.append(sDATE)

    # Does the last date equal to the last day of the month of interest?
    have_all_dates = np.array(Next_DATE) == eDATE

    DOM_DATES = []
    for i in Next_DATE:
        next_sDATE = i
        days = int((eDATE-next_sDATE).days)
        DATES = [next_sDATE+timedelta(days=d) for d in range(days)]
        DOM_DATES.append(DATES)

    days = int((eDATE-sDATE).days)
    DATES = [sDATE+timedelta(days=d) for d in range(days)]

    for DATE in DATES:
        #print(DATE)
        write_domains = []
        for (DOM, DOM_DD) in zip(DOMAINS,DOM_DATES):
            # Do we need this date?
            if DATE in DOM_DD:
                write_domains.append(DOM)
            #print('%s, %s' % (DOM, DD in DOM_DD))
        if len(write_domains) != 0:
            print(write_domains)
            # Get HRRR and GLM lightning binary fields
            stats = get_GLM_HRRR_contingency_stats(DATE)
            
            if stats != None:
                obs_binary = stats.get("Observed Binary")
                fxx_binary = stats.get("Forecast Binary")
            for r in radii:
                if stats != None:
                    FSS = fractions_skill_score_SPECIAL(obs_binary, fxx_binary, domains, radius=r)
                    write_table_to_file(FSS, DATE, write_domains, SAVEDIR='./HRRR_GLM_Fractions_Skill_Score_r%02d/' % r)
                else:
                    write_table_to_file(None, DATE, write_domains, SAVEDIR='./HRRR_GLM_Fractions_Skill_Score_r%02d/' % r)
    return 'Finished %s' % len(DATES)
    

if __name__ == '__main__':
    

    import sys
    sys.path.append('/uufs/chpc.utah.edu/common/home/u0553130/pyBKB_v3')
    from BB_HRRR.GLM_and_HRRR.GLM_events_HRRR import get_GLM_HRRR_contingency_stats,\
                                                     m, Hlat, Hlon, domains

    ## Specify the valid Datetime of interest
    #DATE = datetime(2018, 5, 16, 2) # Mallard Fire
    #DATE = datetime(2018, 7, 5, 21) # Lake Christine
    #DATE = datetime(2018, 7, 17, 6) # July Storm
    #DATE = datetime(2018, 7, 27, 0) # Missing GLM data


    ## Each file will be all days for the hour of that month
    year = 2018
    hours = range(24)

    import socket
    host = socket.gethostname().split('.')[0]

    if host == 'meteo19':
        months = [7]
        hours = [9, 12, 13, 20]
    elif host == 'wx1':
        months = [5]
        hours = range(24)
    elif host == 'wx2':
        months = [6]
        hours = range(24)
    elif host == 'wx3':
        months = [9]
        hours = range(24)
    elif host == 'wx4':
        months = [10]
        hours = range(24)
    elif host == 'meso3':
        #months = [7, 9]
        #hours = range(20)
        months = [8]
        hours = range(0,13)
    elif host == 'meso4':
        months = [8]
        hours = range(13,24)
                
    
    #radii = [5, 10]
    #radii = [20, 40]
    #radii = [40]
    #radii = [60, 80]
    radii = [60, 80]
    

    inputs = [(year, month, hour, radii) for month in months for hour in hours]
        
    status = list(map(write_to_files_MP, inputs))
