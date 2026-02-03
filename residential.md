
# Residential Sector
 The high resolution residential sector contains different appliance, space heating/cooling and lighting technologies for residential housing.
        
##Commodity
| name                 | description                                                                  | type               | units    |
|:---------------------|:-----------------------------------------------------------------------------|:-------------------|:---------|
| R\_ng                | (PJ) natural gas in the residential sector                                   | annual commodity   | nan      |
| R\_lpg               | (PJ) lpg in the residential sector                                           | annual commodity   | nan      |
| R\_wood              | (PJ) wood in the residential sector                                          | annual commodity   | nan      |
| R\_oil               | (PJ) refined petroleum products (residential)                                | annual commodity   | nan      |
| R\_elc               | (PJ) electricity (residential)                                               | annual commodity   | nan      |
| R\_D\_sph            | (PJ) demand for residential space heating                                    | demand commodity   | (PJ)     |
| R\_D\_spc            | (PJ) demand for residential space cooling                                    | demand commodity   | (PJ)     |
| R\_D\_lgt            | (PJ) demand for residential lighting                                         | demand commodity   | (Glmy)   |
| R\_D\_wah            | (PJ) demand for residential water heating                                    | demand commodity   | (PJ)     |
| R\_D\_app\_oth       | (PJ) demand for other residential electrical appliances                      | demand commodity   | (Munity) |
| R\_D\_app\_cook\_rng | (PJ) demand for residential cooking ranges                                   | demand commodity   | (Munity) |
| R\_D\_app\_cdry      | (PJ) demand for residential clothes dryers                                   | demand commodity   | (Munity) |
| R\_D\_app\_ref       | (PJ) demand for residential refrigerators                                    | demand commodity   | (Munity) |
| R\_D\_app\_dsh       | (PJ) demand for residential dish washers                                     | demand commodity   | (Munity) |
| R\_D\_app\_frz       | (PJ) demand for residential freezers                                         | demand commodity   | (Munity) |
| R\_D\_app\_cwsh      | (PJ) demand for residential clothes washers                                  | demand commodity   | (Munity) |
| R\_ethos             | (PJ) dummy input - residential                                               | source commodity   | nan      |

## Technology

| tech                       | description                                                                                           |   unlim_cap |   annual |   reserve |   curtail |   flex |
|:---------------------------|:------------------------------------------------------------------------------------------------------|------------:|---------:|----------:|----------:|-------:|
| F\_R\_OIL                  | Oil distribution from fuel sector to residential sector                                               |           1 |        1 |         0 |         0 |      0 |
| F\_R\_NG                   | Natural gas distribution from fuel sector to residential sector                                       |           1 |        1 |         0 |         0 |      0 |
| E\_R\_ELC                  | Electricity distribution to residential sector                                                        |           1 |        0 |         0 |         0 |      0 |
| F\_R\_LPG                  | Liquefied petroleum gas (lpg) (primarily propane) distribution from fuel sector to residential sector |           1 |        1 |         0 |         0 |      0 |
| F\_R\_WOOD                 | Wood distribution from fuel sector to residential sector                                              |           1 |        1 |         0 |         0 |      0 |
| R\_APP\_CL\_WASH-TYP-NEW   | appliances clothes washers - top loader washing machine typical efficiency - new                      |           0 |        1 |         0 |         0 |      0 |
| R\_APP\_DS\_WASH-TYP-NEW   | appliances dish washers - dish washer typical efficiency - new                                        |           0 |        1 |         0 |         0 |      0 |
| R\_APP\_COOK\_ELEC-TYP-NEW | appliances cooking ranges - electric cooking range typical efficiency - new                           |           0 |        1 |         0 |         0 |      0 |
| R\_APP\_DRY\_ELEC-TYP-NEW  | appliances clothes dryers - electric dryer typical efficiency - new                                   |           0 |        1 |         0 |         0 |      0 |
| R\_APP\_FREZ-TYP-NEW       | appliances freezers - freezer typical efficiency - new                                                |           0 |        1 |         0 |         0 |      0 |
| R\_APP\_COOK\_NG-TYP-NEW   | appliances cooking ranges - natural gas cooking range typical efficiency - new                        |           0 |        1 |         0 |         0 |      0 |
| R\_APP\_DRY\_NG-TYP-NEW    | appliances clothes dryers - natural gas dryer typical efficiency - new                                |           0 |        1 |         0 |         0 |      0 |
| R\_APP\_REFR-TYP-NEW       | appliances refrigerators - refrigerator with bottom-mounted freezer typical efficiency - new          |           0 |        1 |         0 |         0 |      0 |
| R\_SPC\_CENT\_AC-TYP-NEW   | space cooling - central air conditioner typical efficiency - new                                      |           0 |        1 |         0 |         0 |      0 |
| R\_SPC\_ROOM\_AC-TYP-NEW   | space cooling - room air conditioner typical efficiency - new                                         |           0 |        1 |         0 |         0 |      0 |
| R\_SPH\_ELEC\_RAD-NEW      | space heating - electric radiator typical efficiency - new                                            |           0 |        1 |         0 |         0 |      0 |
| R\_SPH\_NG\_FRN-TYP-NEW    | space heating - natural gas furnace typical efficiency - new                                          |           0 |        1 |         0 |         0 |      0 |
| R\_SPH\_WOOD-TYP-NEW       | space heating - wood stove typical efficiency - new                                                   |           0 |        1 |         0 |         0 |      0 |
| R\_SPHC\_AIR\_HP-TYP-NEW   | space heating+space cooling - air-source heat pump typical efficiency - new                           |           0 |        1 |         0 |         0 |      0 |
| R\_SPHC\_GEO\_HP-TYP-NEW   | space heating+space cooling - geo-exchange heat pump typical efficiency - new                         |           0 |        1 |         0 |         0 |      0 |
| R\_SPHC\_NG\_HP-TYP-NEW    | space heating+space cooling - natural gas heat pump typical efficiency - new                          |           0 |        1 |         0 |         0 |      0 |
| R\_WAH\_ELEC-TYP-NEW       | water heating - electric typical efficiency - new                                                     |           0 |        1 |         0 |         0 |      0 |
| R\_WAH\_HP-TYP-NEW         | water heating - heat pump typical efficiency - new                                                    |           0 |        1 |         0 |         0 |      0 |
| R\_WAH\_NG-TYP-NEW         | water heating - natural gas typical efficiency - new                                                  |           0 |        1 |         0 |         0 |      0 |
| R\_WAH\_SOLAR-TYP-NEW      | water heating - solar typical efficiency - new                                                        |           0 |        1 |         0 |         0 |      0 |
| R\_SPH\_OIL-EXS            | space heating - oil - existing                                                                        |           0 |        1 |         0 |         0 |      0 |
| R\_SPH\_NG-EXS             | space heating - natural gas - existing                                                                |           0 |        1 |         0 |         0 |      0 |
| R\_SPH\_ELEC-EXS           | space heating - electric - existing                                                                   |           0 |        1 |         0 |         0 |      0 |
| R\_SPH\_HP-EXS             | space heating - heat pump - existing                                                                  |           0 |        1 |         0 |         0 |      0 |
| R\_SPH\_OTH-EXS            | space heating - other (LPG) - existing                                                                |           0 |        1 |         0 |         0 |      0 |
| R\_SPH\_WOOD-EXS           | space heating - wood - existing                                                                       |           0 |        1 |         0 |         0 |      0 |
| R\_SPH\_WOOD-ELC-EXS       | space heating - dual wood-electric - existing                                                         |           0 |        1 |         0 |         0 |      0 |
| R\_SPH\_WOOD-OIL-EXS       | space heating - dual wood-oil - existing                                                              |           0 |        1 |         0 |         0 |      0 |
| R\_SPH\_NG-ELC-EXS         | space heating - dual natural gas-electric - existing                                                  |           0 |        1 |         0 |         0 |      0 |
| R\_SPH\_OIL-ELC-EXS        | space heating - dual oil-electric - existing                                                          |           0 |        1 |         0 |         0 |      0 |
| R\_SPC\_ROOM\_AC-EXS       | space cooling - room air conditioning - existing                                                      |           0 |        1 |         0 |         0 |      0 |
| R\_SPC\_CENT\_AC-EXS       | space cooling - central air conditioning - existing                                                   |           0 |        1 |         0 |         0 |      0 |
| R\_WAH\_ELC-EXS            | water heating - electric - existing                                                                   |           0 |        1 |         0 |         0 |      0 |
| R\_WAH\_NG-EXS             | water heating - natural gas - existing                                                                |           0 |        1 |         0 |         0 |      0 |
| R\_WAH\_OIL-EXS            | water heating - oil - existing                                                                        |           0 |        1 |         0 |         0 |      0 |
| R\_WAH\_LPG-EXS            | water heating - other (LPG) - existing                                                                |           0 |        1 |         0 |         0 |      0 |
| R\_APP\_CL\_WASH-EXS       | appliances clothes washers - clothes washers - existing                                               |           0 |        1 |         0 |         0 |      0 |
| R\_APP\_DS\_WASH-EXS       | appliances dish washers - dish washers - existing                                                     |           0 |        1 |         0 |         0 |      0 |
| R\_APP\_COOK\_ELEC-EXS     | appliances cooking ranges - electric cooking ranges - existing                                        |           0 |        1 |         0 |         0 |      0 |
| R\_APP\_DRY\_ELEC-EXS      | appliances clothes dryers - electric clothes dryers - existing                                        |           0 |        1 |         0 |         0 |      0 |
| R\_APP\_FREZ-EXS           | appliances freezers - freezers - existing                                                             |           0 |        1 |         0 |         0 |      0 |
| R\_APP\_REFR-EXS           | appliances refrigerators - refrigerators - existing                                                   |           0 |        1 |         0 |         0 |      0 |
| R\_APP\_COOK\_NG-EXS       | appliances cooking ranges - natural gas cooking ranges - existing                                     |           0 |        1 |         0 |         0 |      0 |
| R\_APP\_DRY\_NG-EXS        | appliances clothes dryers - natural gas clothes dryers - existing                                     |           0 |        1 |         0 |         0 |      0 |
| R\_APP\_OTH                | appliances other - other electrical appliances and devices                                            |           1 |        1 |         0 |         0 |      0 |
| R\_LGT\_CFL-EXS            | lighting - compact fluorescent                                                                        |           0 |        1 |         0 |         0 |      0 |
| R\_LGT\_LED-EXS            | lighting - led                                                                                        |           0 |        1 |         0 |         0 |      0 |
| R\_LGT\_T12-EXS            | lighting - linear fluorescent                                                                         |           0 |        1 |         0 |         0 |      0 |
| R\_LGT\_LED-TYP-NEW        | lighting - new led bulb (60W inc-equiv) typical efficiency                                            |           0 |        1 |         0 |         0 |      0 |
